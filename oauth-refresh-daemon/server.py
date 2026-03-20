#!/usr/bin/env python3
"""OAuth Refresh Daemon — manages Google OAuth + GitHub PAT per VM."""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path.home() / ".secrets.env")

# --- Config ---
PORT = int(os.getenv("PORT", "8092"))
GOOGLE_CLIENT_SECRETS_FILE = os.getenv(
    "GOOGLE_CLIENT_SECRETS_FILE",
    str(Path.home() / ".google-oauth-client.json"),
)
GOOGLE_SCOPES = os.getenv(
    "GOOGLE_SCOPES",
    "https://www.googleapis.com/auth/drive",
).split(",")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    f"http://localhost:{PORT}/oauth/google/callback",
)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PAT", "")
TOKEN_PATH = Path(os.getenv("TOKEN_PATH", str(Path.home() / ".oauth-tokens.json")))
REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "30"))

token_lock = Lock()
scheduler = BackgroundScheduler()


# --- Token I/O ---
def load_tokens() -> dict:
    if TOKEN_PATH.exists():
        try:
            return json.loads(TOKEN_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_tokens(tokens: dict):
    TOKEN_PATH.write_text(json.dumps(tokens, indent=2, default=str))


# --- Google refresh ---
def refresh_google_token():
    tokens = load_tokens()
    google = tokens.get("google", {})
    if not google.get("token_json"):
        return

    try:
        creds = Credentials.from_authorized_user_info(
            json.loads(google["token_json"]), GOOGLE_SCOPES
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            with token_lock:
                tokens["google"]["token_json"] = creds.to_json()
                tokens["google"]["expires_at"] = (
                    creds.expiry.isoformat() if creds.expiry else None
                )
                save_tokens(tokens)
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] Google token refreshed")
    except Exception as e:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Google refresh failed: {e}")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed GitHub PAT from env on startup
    if GITHUB_TOKEN:
        tokens = load_tokens()
        tokens["github"] = {"token": GITHUB_TOKEN, "type": "pat"}
        save_tokens(tokens)
    scheduler.add_job(refresh_google_token, "interval", minutes=REFRESH_INTERVAL_MINUTES)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="OAuth Refresh Daemon", lifespan=lifespan)


# --- Web UI ---
def _status_badge(ok: bool) -> str:
    color = "green" if ok else "red"
    label = "OK" if ok else "Not configured"
    return f'<span style="color:{color}">{label}</span>'


@app.get("/", response_class=HTMLResponse)
def index():
    tokens = load_tokens()
    google = tokens.get("google", {})
    github = tokens.get("github", {})
    google_valid = bool(google.get("token_json"))
    google_expiry = google.get("expires_at", "—")
    github_valid = bool(github.get("token"))

    return f"""<!DOCTYPE html>
<html><head><title>OAuth Manager</title><style>
body{{font-family:monospace;max-width:640px;margin:40px auto;padding:0 20px;background:#111;color:#eee}}
h1{{font-size:1.1rem;border-bottom:1px solid #444;padding-bottom:8px}}
h2{{font-size:0.95rem;margin-top:24px;color:#aaa}}
p{{margin:4px 0}}
a.btn{{display:inline-block;padding:6px 14px;background:#333;color:#eee;text-decoration:none;border-radius:4px;margin:8px 4px 0 0;font-size:0.85rem;border:1px solid #555}}
a.btn:hover{{background:#444}}
</style></head>
<body>
<h1>OAuth Refresh Daemon — port {PORT}</h1>

<h2>Google OAuth</h2>
<p>Status: {_status_badge(google_valid)}</p>
<p>Expires: {google_expiry}</p>
<a class="btn" href="/oauth/google/authorize">Authorize Google</a>

<h2>GitHub</h2>
<p>Status: {_status_badge(github_valid)}</p>
<p>PAT loaded from GITHUB_TOKEN env var.</p>

<h2>Actions</h2>
<a class="btn" href="/oauth/status">Status (JSON)</a>
<a class="btn" href="#" onclick="fetch('/oauth/refresh',{{method:'POST'}}).then(()=>location.reload());return false">Force Refresh</a>
</body></html>"""


# --- OAuth routes ---
@app.get("/oauth/google/authorize")
def google_authorize():
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return RedirectResponse(auth_url)


@app.get("/oauth/google/callback", response_class=HTMLResponse)
def google_callback(code: str = None, state: str = None, error: str = None, error_description: str = None):
    if error:
        msg = error_description or error
        return f"""<html><body style="font-family:monospace;background:#111;color:#f66;padding:40px">
<h2>Google OAuth Error</h2><p>{msg}</p>
<p>error code: <b>{error}</b></p>
<p>Redirect URI used: <b>{GOOGLE_REDIRECT_URI}</b></p>
<p>Make sure this URI is registered in Google Cloud Console under your OAuth client.</p>
<a href="/" style="color:#aaa">← Back</a></body></html>"""

    if not code:
        return HTMLResponse("<html><body>Missing code parameter.</body></html>", status_code=400)

    try:
        flow = Flow.from_client_secrets_file(
            GOOGLE_CLIENT_SECRETS_FILE,
            scopes=GOOGLE_SCOPES,
            redirect_uri=GOOGLE_REDIRECT_URI,
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        with token_lock:
            tokens = load_tokens()
            tokens["google"] = {
                "token_json": creds.to_json(),
                "expires_at": creds.expiry.isoformat() if creds.expiry else None,
                "scopes": GOOGLE_SCOPES,
            }
            save_tokens(tokens)

        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Google OAuth flow completed")
        return RedirectResponse("/")
    except Exception as e:
        return HTMLResponse(f"""<html><body style="font-family:monospace;background:#111;color:#f66;padding:40px">
<h2>OAuth callback failed</h2><p>{e}</p><a href="/" style="color:#aaa">← Back</a>
</body></html>""", status_code=500)


# --- API endpoints ---
@app.get("/oauth/status")
def oauth_status():
    tokens = load_tokens()
    google = tokens.get("google", {})
    github = tokens.get("github", {})

    google_valid = False
    google_expiry = None
    if google.get("token_json"):
        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(google["token_json"]), GOOGLE_SCOPES
            )
            google_valid = not creds.expired or bool(creds.refresh_token)
            google_expiry = creds.expiry.isoformat() if creds.expiry else None
        except Exception:
            pass

    return {
        "google": {"valid": google_valid, "expires_at": google_expiry},
        "github": {"valid": bool(github.get("token")), "type": github.get("type", "none")},
    }


@app.get("/oauth/tokens")
def get_tokens():
    """Return live tokens for consuming services."""
    tokens = load_tokens()
    result = {}

    google = tokens.get("google", {})
    if google.get("token_json"):
        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(google["token_json"]), GOOGLE_SCOPES
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(GoogleRequest())
                with token_lock:
                    tokens["google"]["token_json"] = creds.to_json()
                    tokens["google"]["expires_at"] = (
                        creds.expiry.isoformat() if creds.expiry else None
                    )
                    save_tokens(tokens)
            result["google"] = {
                "access_token": creds.token,
                "expires_at": creds.expiry.isoformat() if creds.expiry else None,
            }
        except Exception as e:
            result["google"] = {"error": str(e)}

    github = tokens.get("github", {})
    if github.get("token"):
        result["github"] = {"token": github["token"], "type": "pat"}

    return result


@app.post("/oauth/refresh")
def force_refresh():
    refresh_google_token()
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
