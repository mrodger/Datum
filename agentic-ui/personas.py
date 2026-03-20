"""Load persona metadata from ~/vault/personas/"""
import re
from pathlib import Path

PERSONAS_DIR = Path.home() / "vault" / "personas"

DEFAULT_PERSONA = "chat"

DISPLAY_NAMES = {
    "openai": "OpenAI",
    "gis-analyst": "GIS Analyst",
}

ICONS = {
    "chat":         "💬",
    "assistant":    "◆",
    "developer":    "⌨",
    "researcher":   "🔍",
    "gis-analyst":  "🗺",
    "sysadmin":     "⚙",
    "outreach":     "📣",
    "designer":     "✏",
    "entrepreneur": "💡",
    "manager":      "📋",
    "security":     "🔒",
    "openai":       "⬡",
}


def load_personas():
    personas = []
    if not PERSONAS_DIR.exists():
        return personas
    paths = sorted(PERSONAS_DIR.glob("*.md"), key=lambda p: (p.stem != DEFAULT_PERSONA, p.stem))
    for path in paths:
        name = path.stem
        display = DISPLAY_NAMES.get(name, name.replace("-", " ").title())
        role = ""
        try:
            text = path.read_text()
            m = re.search(r"## Role\n(.+)", text)
            if m:
                role = m.group(1).strip()
        except Exception:
            pass
        personas.append({
            "id": name,
            "display": display,
            "role": role,
            "icon": ICONS.get(name, "◇"),
        })
    return personas


def get_persona(name):
    for p in load_personas():
        if p["id"] == name:
            return p
    return None
