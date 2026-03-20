"""File tools for the OpenAI agent — all paths are jailed to /workspace."""
from pathlib import Path

WORKSPACE = Path("/workspace")


def _safe_path(path: str) -> Path:
    """Resolve a relative path within WORKSPACE; raise if it escapes."""
    p = (WORKSPACE / path.lstrip("/")).resolve()
    if not str(p).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path escapes workspace: {path!r}")
    return p


def read_file(path: str) -> str:
    try:
        return _safe_path(path).read_text(errors="replace")
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    try:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"OK: wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def list_directory(path: str = "") -> str:
    try:
        p = _safe_path(path) if path else WORKSPACE
        if not p.exists():
            return f"Error: directory not found: {path}"
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        if not entries:
            return "(empty)"
        lines = []
        for entry in entries:
            tag = "dir " if entry.is_dir() else "file"
            lines.append(f"[{tag}] {entry.name}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


TOOL_MAP = {
    "read_file":      lambda a: read_file(a["path"]),
    "write_file":     lambda a: write_file(a["path"], a["content"]),
    "list_directory": lambda a: list_directory(a.get("path", "")),
}


def execute_tool(name: str, args: dict) -> str:
    fn = TOOL_MAP.get(name)
    if not fn:
        return f"Error: unknown tool {name!r}"
    return fn(args)
