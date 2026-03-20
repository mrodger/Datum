#!/usr/bin/env python3
import asyncio
import io
import json
import mimetypes
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import db
import personas as persona_mod

# ── Google Drive helpers ───────────────────────────────────────────────────────

GOOGLE_TOKEN_PATH = Path.home() / ".google-token.json"

INLINE_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".sass",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".csv", ".tsv", ".xml", ".svg",
    ".sh", ".bash", ".zsh", ".env", ".gitignore",
    ".sql", ".graphql", ".proto", ".tf",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java",
    ".kt", ".rb", ".php", ".swift", ".dart", ".r",
}
INLINE_MAX_BYTES = 100 * 1024  # 100 KB


def _is_inline(filename: str, size: int) -> bool:
    if size > INLINE_MAX_BYTES:
        return False
    return Path(filename).suffix.lower() in INLINE_EXTENSIONS


def _drive_service():
    creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        GOOGLE_TOKEN_PATH.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_or_create_folder(svc, name: str, parent_id: str = None) -> str:
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    results = svc.files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return svc.files().create(body=meta, fields="id").execute()["id"]


def _upload_to_drive(content: bytes, filename: str, mime_type: str, persona_display: str) -> dict:
    svc = _drive_service()
    root_id = _get_or_create_folder(svc, "AgentUI")
    folder_id = _get_or_create_folder(svc, persona_display, root_id)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
    result = svc.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink,name",
    ).execute()
    return {"file_id": result["id"], "url": result.get("webViewLink", ""), "name": result["name"]}

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path.home() / ".secrets.env")
db.init_db()

app = FastAPI(title="agentic-ui")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ── Claude Code subprocess ────────────────────────────────────────────────────

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
OPENAI_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini", "o3-mini"}
OPENAI_AGENT_URL = os.environ.get("OPENAI_AGENT_URL", "http://localhost:8091")

async def run_cc(prompt: str, session_id: str = None, model: str = None, persona: str = None):
    """
    Run `claude -p <prompt>` with stream-json output.
    Yields dicts:
      {type: "text",     text: str}
      {type: "tool_use", name: str, input: dict}
      {type: "done",     session_id: str, cost_usd: float}
      {type: "error",    detail: str}
    """
    # Prepend persona context if provided
    if persona and prompt.strip() == "init":
        prompt = f"init --persona {persona}"

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model or DEFAULT_MODEL,
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd += ["--resume", session_id]

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        limit=10 * 1024 * 1024,  # 10MB — default 64KB overflows on large tool outputs
    )

    new_session_id = None
    cost_usd = 0.0
    input_tokens = 0
    output_tokens = 0

    async for raw in proc.stdout:
        line = raw.decode().strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")

        if etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    yield {"type": "text", "text": block["text"]}
                elif btype == "tool_use":
                    yield {
                        "type": "tool_use",
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    }

        elif etype == "result":
            new_session_id = event.get("session_id")
            cost_usd = event.get("total_cost_usd", 0.0)
            usage = event.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            if event.get("subtype") != "success":
                yield {"type": "error", "detail": event.get("result", "Unknown error")}
                return

    await proc.wait()

    if proc.returncode != 0 and not new_session_id:
        stderr = (await proc.stderr.read()).decode().strip()
        yield {"type": "error", "detail": stderr or "claude process failed"}
        return

    yield {"type": "done", "session_id": new_session_id, "cost_usd": cost_usd,
           "input_tokens": input_tokens, "output_tokens": output_tokens}


# ── OpenAI Agent (Docker container proxy) ────────────────────────────────────

async def run_openai_agent(messages: list, model: str = "gpt-4o"):
    """
    Proxy to the openai-agent Docker container running on port 8091.
    Yields the same event dicts as run_cc / run_openai.
    """
    try:
        import httpx
    except ImportError:
        yield {"type": "error", "detail": "httpx not installed — run: pip install httpx"}
        return

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{OPENAI_AGENT_URL}/chat",
                json={"messages": messages, "model": model},
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    yield {"type": "error", "detail": f"openai-agent {response.status_code}: {body.decode()[:200]}"}
                    return
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        yield json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        yield {"type": "error", "detail": f"openai-agent unreachable: {e}"}


# ── OpenAI streaming ──────────────────────────────────────────────────────────

async def run_openai(prompt: str, model: str, history: list):
    """
    Call OpenAI chat completions with streaming.
    history: list of {role, content} dicts from DB (excluding current prompt).
    Yields same event shape as run_cc:
      {type: "text",  text: str}
      {type: "done",  session_id: None, cost_usd: float, input_tokens: int, output_tokens: int}
      {type: "error", detail: str}
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        yield {"type": "error", "detail": "openai package not installed — run: pip install openai"}
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        yield {"type": "error", "detail": "OPENAI_API_KEY not set in environment"}
        return

    client = AsyncOpenAI(api_key=api_key)

    messages = [{"role": m["role"], "content": m["content"]}
                for m in history if m["role"] in ("user", "assistant") and m["content"] != "init"]
    messages.append({"role": "user", "content": prompt})

    input_tokens = 0
    output_tokens = 0

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield {"type": "text", "text": chunk.choices[0].delta.content}
            if chunk.usage:
                input_tokens  = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

    except Exception as e:
        yield {"type": "error", "detail": str(e)}
        return

    # Approximate cost (GPT-4o rates; good enough for display)
    PRICING = {
        "gpt-4o":       (2.50, 10.00),
        "gpt-4o-mini":  (0.15,  0.60),
        "gpt-4.1":      (2.00,  8.00),
        "gpt-4.1-mini": (0.40,  1.60),
        "o4-mini":      (1.10,  4.40),
        "o3-mini":      (1.10,  4.40),
    }
    in_rate, out_rate = PRICING.get(model, (2.50, 10.00))
    cost_usd = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000

    yield {"type": "done", "session_id": None, "cost_usd": cost_usd,
           "input_tokens": input_tokens, "output_tokens": output_tokens}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
def index():
    return static_dir / "index.html"


@app.get("/api/personas")
def list_personas():
    return {
        "default": persona_mod.DEFAULT_PERSONA,
        "personas": persona_mod.load_personas(),
    }


@app.get("/api/conversations")
def list_conversations(persona: str = None):
    return db.list_conversations(persona)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404)
    return conv


@app.delete("/api/conversations/{cid}", status_code=204)
def delete_conversation(cid: str):
    db.delete_conversation(cid)




class PatchConversation(BaseModel):
    title: str


@app.patch("/api/conversations/{cid}", status_code=200)
def patch_conversation(cid: str, body: PatchConversation):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404)
    title = body.title.strip()[:100] or "New Chat"
    db.update_conversation(cid, title=title)
    return db.get_conversation(cid)

@app.get("/api/conversations/{cid}/messages")
def get_messages(cid: str):
    if not db.get_conversation(cid):
        raise HTTPException(404)
    return db.get_messages(cid)


class NewConversation(BaseModel):
    persona: str


@app.post("/api/conversations", status_code=201)
def create_conversation(body: NewConversation):
    p = persona_mod.get_persona(body.persona)
    if not p:
        raise HTTPException(400, f"Unknown persona: {body.persona}")
    conv = db.create_conversation(body.persona)
    return conv


class SendMessage(BaseModel):
    content: str
    model: str = DEFAULT_MODEL
    provider: str = "claude"  # "claude" | "openai"


@app.post("/api/conversations/{cid}/messages")
async def send_message(cid: str, body: SendMessage):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404)

    db.add_message(cid, "user", body.content)
    session_id = conv.get("cc_session_id")
    all_msgs = db.get_messages(cid)
    real_user_msgs = [m for m in all_msgs if m["role"] == "user" and m["content"] != "init"]
    should_auto_title = (
        len(real_user_msgs) == 2
        and conv.get("title") == "New Chat"
    )
    first_real_content = real_user_msgs[0]["content"] if real_user_msgs else ""

    persona = conv.get("persona")
    use_openai_agent = persona == "openai"
    use_openai = not use_openai_agent and (body.provider == "openai" or body.model in OPENAI_MODELS)

    async def stream():
        text_buf = ""
        tool_calls = []

        try:
            if use_openai_agent:
                history = [m for m in all_msgs if m["role"] in ("user", "assistant")]
                agent_model = body.model if body.model in OPENAI_MODELS else "gpt-4o"
                event_iter = run_openai_agent(history, agent_model)
            elif use_openai:
                history = [m for m in all_msgs if m["role"] in ("user", "assistant")]
                # exclude the message we just added (it's appended in run_openai)
                history = history[:-1]
                event_iter = run_openai(body.content, body.model, history)
            else:
                event_iter = run_cc(body.content, session_id, body.model, persona)
            async for event in event_iter:

                if event["type"] == "text":
                    text_buf += event["text"]
                    yield {"event": "delta", "data": json.dumps({"text": event["text"]})}

                elif event["type"] == "tool_use":
                    tool_calls.append({
                        "name": event["name"],
                        "input": event["input"],
                    })
                    yield {"event": "tool", "data": json.dumps({
                        "name": event["name"],
                        "input": event["input"],
                    })}

                elif event["type"] == "done":
                    db.add_message(
                        cid, "assistant", text_buf,
                        tool_calls=tool_calls or None,
                        cost_usd=event["cost_usd"],
                        input_tokens=event.get("input_tokens", 0),
                        output_tokens=event.get("output_tokens", 0),
                    )
                    db.update_conversation(
                        cid,
                        cc_session_id=event.get("session_id"),
                        cost_usd=event["cost_usd"],
                    )
                    # Auto-title after 2nd real user message
                    if should_auto_title:
                        title = first_real_content[:60].strip()
                        if len(first_real_content) > 60:
                            title += "…"
                        db.update_conversation(cid, title=title)

                    yield {"event": "done", "data": json.dumps({
                        "cost_usd": event["cost_usd"],
                        "input_tokens": event.get("input_tokens", 0),
                        "output_tokens": event.get("output_tokens", 0),
                        "title": db.get_conversation(cid)["title"],
                    })}

                elif event["type"] == "error":
                    yield {"event": "error", "data": json.dumps({"detail": event["detail"]})}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"detail": str(e)})}

    return EventSourceResponse(stream())


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), persona: str = Form(...)):
    content = await file.read()
    filename = file.filename or "upload"
    mime_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    if _is_inline(filename, len(content)):
        text = content.decode("utf-8", errors="replace")
        return {"mode": "inline", "filename": filename, "content": text, "size": len(content)}

    p = persona_mod.get_persona(persona)
    persona_display = p["display"] if p else persona.replace("-", " ").title()
    try:
        result = _upload_to_drive(content, filename, mime_type, persona_display)
        return {"mode": "drive", "filename": filename,
                "file_id": result["file_id"], "url": result["url"], "size": len(content)}
    except Exception as e:
        raise HTTPException(500, f"Drive upload failed: {e}")


# ── Voice TTS ──────────────────────────────────────────────────────────────────

class TextToSpeech(BaseModel):
    text: str


@app.post("/api/voice/synthesize")
def synthesize_speech(body: TextToSpeech):
    """Generate WAV audio from text. Returns file path and metadata."""
    if not body.text or len(body.text.strip()) == 0:
        raise HTTPException(400, "text cannot be empty")

    if len(body.text) > 10000:
        raise HTTPException(400, "text too long (max 10000 chars)")

    # Call voice_tts.py skill
    skill_path = Path.home() / ".claude" / "skills" / "voice-tts" / "voice_tts.py"

    try:
        result = subprocess.run(
            ["python3", str(skill_path), "generate", body.text],
            capture_output=True,
            timeout=30,
            text=True,
        )

        if result.returncode != 0:
            raise HTTPException(500, f"Voice generation failed: {result.stderr}")

        response = json.loads(result.stdout)
        if response.get("status") != "ok":
            raise HTTPException(500, response.get("detail", "Unknown error"))

        # Add serve URL to response
        import hashlib
        text_hash = hashlib.sha256(body.text.encode()).hexdigest()[:12]
        response["url"] = f"/api/voice/audio/{text_hash}.wav"

        return response

    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Voice generation timed out")
    except json.JSONDecodeError:
        raise HTTPException(500, "Invalid response from voice generator")
    except Exception as e:
        raise HTTPException(500, f"Voice generation error: {str(e)}")


@app.get("/api/voice/audio/{filename}")
def get_audio(filename: str):
    """Serve generated audio file."""
    # Security: only allow .wav files from cache dir
    if not filename.endswith(".wav"):
        raise HTTPException(400, "Only WAV files supported")

    audio_path = Path.home() / ".cache" / "voice_tts" / filename

    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")

    return FileResponse(str(audio_path), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8090))
    uvicorn.run("server:app", host=host, port=port, reload=False)
