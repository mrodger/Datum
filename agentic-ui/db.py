import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "agentic-ui.db"


def get_conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        # Migrate existing DB — add token columns if missing
        for col_def in (
            "input_tokens INTEGER NOT NULL DEFAULT 0",
            "output_tokens INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col_def}")
            except Exception:
                pass
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id            TEXT PRIMARY KEY,
                persona       TEXT NOT NULL,
                title         TEXT NOT NULL DEFAULT 'New Chat',
                cc_session_id TEXT,
                cost_usd      REAL NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content         TEXT NOT NULL DEFAULT '',
                tool_calls      TEXT,
                cost_usd        REAL,
                input_tokens    INTEGER NOT NULL DEFAULT 0,
                output_tokens   INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_conv_persona
                ON conversations(persona, updated_at);
        """)


def now():
    return datetime.now(timezone.utc).isoformat()


# ── Conversations ─────────────────────────────────────────────────────────────

def create_conversation(persona):
    cid = str(uuid.uuid4())
    ts = now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (id, persona, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (cid, persona, "New Chat", ts, ts),
        )
    return get_conversation(cid)


def get_conversation(cid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None


def list_conversations(persona=None):
    with get_conn() as conn:
        if persona:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE persona=? ORDER BY updated_at DESC",
                (persona,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def update_conversation(cid, cc_session_id=None, cost_usd=0, title=None):
    with get_conn() as conn:
        if title:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title, now(), cid)
            )
        if cc_session_id or cost_usd:
            conn.execute(
                """UPDATE conversations
                   SET cc_session_id=COALESCE(?,cc_session_id),
                       cost_usd=cost_usd+?,
                       updated_at=?
                   WHERE id=?""",
                (cc_session_id, cost_usd, now(), cid)
            )


def delete_conversation(cid):
    with get_conn() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (cid,))


# ── Messages ──────────────────────────────────────────────────────────────────

def add_message(conv_id, role, content="", tool_calls=None, cost_usd=None,
                input_tokens=0, output_tokens=0):
    mid = str(uuid.uuid4())
    import json
    tc = json.dumps(tool_calls) if tool_calls else None
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, tool_calls, cost_usd,
                input_tokens, output_tokens, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (mid, conv_id, role, content, tc, cost_usd,
             input_tokens, output_tokens, now()),
        )
    return mid


def get_messages(conv_id):
    import json
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
            (conv_id,)
        ).fetchall()
    result = []
    for r in rows:
        m = dict(r)
        if m["tool_calls"]:
            m["tool_calls"] = json.loads(m["tool_calls"])
        result.append(m)
    return result
