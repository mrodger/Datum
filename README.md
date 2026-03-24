# DATUM

A multi-model agentic chat platform that unifies Claude, OpenAI GPT, and custom function-calling agents under a single streaming UI. Built for a personal homelab but designed with production-grade patterns throughout.

---

## What It Does

A single web interface talks to multiple AI backends — you pick a *persona*, and the platform routes your conversation to the right agent, streams the response in real time, and tracks cost and history automatically.

**Personas** are defined as Markdown files, each describing a role and toolset. The platform loads them dynamically — no code changes needed to add a new agent personality.

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (PWA)                        │
│         Vanilla JS · SSE streaming · IndexedDB          │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP + SSE
┌────────────────────────▼────────────────────────────────┐
│              agentic-ui  (FastAPI · port 8090)          │
│                                                         │
│   ┌─ Persona Router ──────────────────────────────┐     │
│   │  claude  →  run_cc()      subprocess + JSON   │     │
│   │  openai  →  run_openai_agent()  httpx proxy   │     │
│   │  *       →  run_openai()  direct SDK stream   │     │
│   └───────────────────────────────────────────────┘     │
│   SQLite · Google Drive upload · Voice TTS              │
└──────────────┬──────────────────────────────────────────┘
               │ HTTP
┌──────────────▼──────────────────┐  ┌─────────────────────┐
│  openai-agent  (Docker · 8091)  │  │  oauth-refresh      │
│  GPT function-calling loop      │  │  (FastAPI · 8092)   │
│  Jailed file workspace          │  │  Google + GitHub    │
│  Tool: read/write/list files    │  │  token daemon       │
└─────────────────────────────────┘  └─────────────────────┘
```

---

## Features

| Feature | Detail |
|---------|--------|
| **Multi-provider streaming** | Claude Code CLI, OpenAI API, custom Docker agents — same SSE format |
| **Persona system** | Role definitions in Markdown; hot-loaded on each request |
| **Conversation persistence** | SQLite with per-message token counts and cost tracking |
| **File uploads** | Text files inlined into prompt; binaries uploaded to Google Drive |
| **Tool call visualisation** | Streaming tool invocations rendered as expandable blocks |
| **PWA-ready** | Installable, offline-capable, works on mobile |
| **OAuth token daemon** | Background token refresh for Google and GitHub — no manual re-auth |

---

## Stack

**Backend**
- [FastAPI](https://fastapi.tiangolo.com/) + [sse-starlette](https://github.com/sysid/sse-starlette) — async HTTP + real-time streaming
- SQLite (WAL mode, no ORM) — conversation and message persistence
- [APScheduler](https://apscheduler.readthedocs.io/) — background token refresh in the OAuth daemon
- [google-auth-oauthlib](https://google-auth-oauthlib.readthedocs.io/) — Google Drive integration

**Frontend**
- Vanilla JavaScript — no build step, no framework
- CSS custom properties — warm `#f9f7f2` linen theme, responsive sidebar
- Service Worker — PWA caching and offline support

**Infrastructure**
- Docker / Docker Compose — isolated OpenAI agent workspace
- systemd user services — auto-start on boot for all three services
- Two-VM deploy pattern — `dev` branch on VM 105 (TEST), `main` on VM 102 (PROD)

---

## Project Structure

```
son-of-anton/
├── agentic-ui/          # Main chat server (port 8090)
│   ├── server.py        # FastAPI routes, streaming, Drive integration
│   ├── personas.py      # Persona loader
│   ├── db.py            # SQLite CRUD
│   └── static/
│       ├── app.js       # 700+ line SPA: state, SSE, rendering
│       ├── style.css    # Responsive layout, theming
│       ├── index.html   # PWA shell
│       └── sw.js        # Service worker
├── openai-agent/        # Docker container (port 8091)
│   ├── agent.py         # Raw OpenAI function-calling loop
│   ├── tools.py         # Jailed file workspace tools
│   └── Dockerfile
├── oauth-refresh-daemon/ # Token manager (port 8092)
│   └── server.py        # OAuth flows + APScheduler refresh
└── deploy/
    ├── deploy.sh        # Push to PROD or TEST and update state
    └── state.json       # Tracks deployed commit per environment
```

---

## Key Design Patterns

### 1. Unified SSE event format

All three backends emit the same event schema, so the frontend doesn't need to know which provider it's talking to:

```python
# text chunk
yield {"event": "message", "data": json.dumps({"type": "text", "text": chunk})}

# tool invocation
yield {"event": "message", "data": json.dumps({
    "type": "tool_use", "name": tool_name, "input": tool_input
})}

# finalise with cost
yield {"event": "message", "data": json.dumps({
    "type": "done", "cost_usd": 0.0023,
    "input_tokens": 412, "output_tokens": 87
})}
```

Frontend processes the stream with a single reader regardless of backend:

```javascript
const reader = resp.body.getReader();
for await (const chunk of readSSE(reader)) {
    const ev = JSON.parse(chunk);
    if (ev.type === "text")     appendText(ev.text);
    if (ev.type === "tool_use") insertToolBlock(ev.name, ev.input);
    if (ev.type === "done")     finaliseBubble(ev.cost_usd);
}
```

### 2. Raw OpenAI function-calling loop

The OpenAI agent runs a manual tool loop — no framework, just the API:

```python
# agent.py
async def run_agent(messages, model, api_key):
    client = AsyncOpenAI(api_key=api_key)
    for _ in range(MAX_TURNS):
        response = await client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS, stream=True
        )
        text, tool_calls = "", []
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                text += delta.content
                yield {"type": "text", "text": delta.content}
            if delta.tool_calls:
                tool_calls.extend(delta.tool_calls)

        if not tool_calls:
            break

        # execute tools and feed results back
        for call in tool_calls:
            result = execute_tool(call.function.name, call.function.arguments)
            yield {"type": "tool_use", "name": call.function.name, "input": ...}
            messages.append({"role": "tool", "content": result, ...})
```

### 3. Persona routing

Personas are plain Markdown files. The router dispatches to the right provider at message time:

```python
# personas.py — loads ~/vault/personas/*.md dynamically
def load_personas() -> list[dict]:
    return [parse_persona(f) for f in Path("~/vault/personas").glob("*.md")]

# server.py — route by persona ID
if persona == "openai":
    event_iter = run_openai_agent(history, model)
elif model in OPENAI_MODELS:
    event_iter = run_openai(content, model, history)
else:
    event_iter = run_cc(content, session_id, model, persona)
```

### 4. Jailed file workspace

The OpenAI agent's tools enforce path containment — no escaping to the host:

```python
# tools.py
WORKSPACE = Path("/workspace")

def _safe_path(path: str) -> Path:
    resolved = (WORKSPACE / path).resolve()
    if not str(resolved).startswith(str(WORKSPACE)):
        raise ValueError(f"Path escape attempt: {path}")
    return resolved

def read_file(path: str) -> str:
    return _safe_path(path).read_text()

def write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"written {len(content)} bytes to {path}"
```

### 5. OAuth token daemon

The daemon handles Google token refresh on a schedule so nothing else needs to think about auth:

```python
# oauth-refresh-daemon/server.py
scheduler.add_job(refresh_google_token, "interval", minutes=30)

def refresh_google_token():
    creds = Credentials.from_authorized_user_info(load_tokens()["google"]["token_json"])
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_tokens(...)  # writes ~/.oauth-tokens.json

# Any service queries /oauth/tokens for a live access token
GET http://localhost:8092/oauth/tokens
→ {"google": {"access_token": "ya29...", "expires_at": "..."}, "github": {...}}
```

---

## Setup

### Prerequisites
- Python 3.12
- Docker + Docker Compose
- Claude Code CLI (`claude`)
- OpenAI API key

### 1. agentic-ui

```bash
cd agentic-ui
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set HOST, PORT
python server.py       # http://localhost:8090
```

### 2. openai-agent

```bash
cd openai-agent
cp .env.example .env   # set OPENAI_API_KEY
docker compose up -d
curl http://localhost:8091/health
```

### 3. oauth-refresh-daemon

```bash
cd oauth-refresh-daemon
bash setup.sh          # creates venv, .env from example
# edit .env: set PORT, GOOGLE_REDIRECT_URI
# set GITHUB_TOKEN or GITHUB_PAT in .env or ~/.secrets.env
systemctl --user enable oauth-refresh.service
systemctl --user start oauth-refresh.service

# One-time Google auth (SSH tunnel to localhost:PORT, then open browser):
# http://localhost:8092/oauth/google/authorize
```

### 4. systemd (auto-start)

Each service ships with a systemd user unit. Enable all three:

```bash
systemctl --user enable agentic-ui.service openai-agent.service oauth-refresh.service
systemctl --user start  agentic-ui.service openai-agent.service oauth-refresh.service
```

---

## PROD / TEST Deployment

Two VMs track the two git branches:

| Branch | Environment | VM |
|--------|-------------|-----|
| `main` | PROD | VM 102 (192.168.88.102) |
| `dev`  | TEST | VM 105 (192.168.88.105) |

Use the deploy script to push changes and update state:

```bash
# deploy to TEST
./deploy/deploy.sh test

# promote TEST → PROD
git checkout main && git merge dev
./deploy/deploy.sh prod
```

Current state of each environment is tracked in `deploy/state.json`.

---

## Extending

**Add a new persona:** create `~/vault/personas/<name>.md` with a `## Role` section. The platform picks it up on the next request — no restart needed.

**Add a new agent backend:**
1. Write a FastAPI container (copy `openai-agent/` as template)
2. Add a proxy function in `agentic-ui/server.py` (copy `run_openai_agent()`)
3. Add routing logic in the `send_message` handler
4. Add icon + display name in `personas.py`

**Add a tool to the OpenAI agent:**
1. Implement the function in `openai-agent/tools.py`
2. Add the schema entry to `TOOLS` in `agent.py`
3. Rebuild: `docker compose build && docker compose up -d`

---

## Roadmap

- [ ] Mobile-optimised view (hamburger drawer, visualViewport fix)
- [ ] Conversation delete from sidebar UI
- [ ] File output download
- [ ] SQLite → PostGIS migration (VM 104)
- [ ] agentic-ui queries oauth-refresh-daemon instead of reading token file directly

---

## License

Private. Not for distribution.
