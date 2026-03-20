#!/usr/bin/env bash
# Setup oauth-refresh-daemon venv and .env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv"

echo "==> Creating venv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "    Done."

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "==> Created .env from example — edit PORT and credentials before starting."
else
    echo "==> .env already exists, skipping."
fi

echo ""
echo "Next steps:"
echo "  1. Edit $SCRIPT_DIR/.env (set PORT, GITHUB_TOKEN, GOOGLE_REDIRECT_URI)"
echo "  2. systemctl --user enable oauth-refresh.service"
echo "  3. systemctl --user start oauth-refresh.service"
echo "  4. Open http://localhost:\$(grep ^PORT $SCRIPT_DIR/.env | cut -d= -f2)/"
