#!/usr/bin/env bash
# deploy.sh — push son-of-anton to PROD or TEST, update state.json
#
# Usage:
#   ./deploy/deploy.sh test     # deploy dev branch to VM 105
#   ./deploy/deploy.sh prod     # deploy main branch to VM 102
#   ./deploy/deploy.sh status   # show current state

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_FILE="$REPO_ROOT/deploy/state.json"

PROD_VM="192.168.88.102"
TEST_VM="192.168.88.105"
PROD_BRANCH="main"
DEV_BRANCH="dev"
REMOTE_DIR="~/projects/son-of-anton"

# Services to restart after deploy (systemd --user)
SERVICES="agentic-ui.service openai-agent.service"

# ── Helpers ──────────────────────────────────────────────────────────────────

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

show_status() {
    echo ""
    echo "=== Deployment State ==="
    python3 -c "
import json, sys
s = json.load(open('$STATE_FILE'))
for env, info in s.items():
    commit = info['commit'] or 'not deployed'
    ts     = info['deployed_at'] or '—'
    print(f\"  {env.upper():6}  {info['vm']:16}  branch={info['branch']:6}  commit={commit[:8] if info['commit'] else 'none':8}  at={ts}\")
"
    echo ""
}

update_state() {
    local env="$1" commit="$2" ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    python3 -c "
import json
s = json.load(open('$STATE_FILE'))
s['$env']['commit'] = '$commit'
s['$env']['deployed_at'] = '$ts'
open('$STATE_FILE', 'w').write(json.dumps(s, indent=2) + '\n')
"
    log "State updated: $env → $commit"
}

deploy_to() {
    local env="$1" vm="$2" branch="$3"

    log "Deploying to $env ($vm) from branch $branch..."

    # Confirm current branch matches expected
    current_branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
    if [[ "$current_branch" != "$branch" ]]; then
        echo "ERROR: Current branch is '$current_branch', expected '$branch'."
        echo "       Run: git checkout $branch"
        exit 1
    fi

    # Confirm no uncommitted changes
    if ! git -C "$REPO_ROOT" diff --quiet HEAD; then
        echo "ERROR: Uncommitted changes. Commit or stash first."
        exit 1
    fi

    commit=$(git -C "$REPO_ROOT" rev-parse HEAD)
    short=$(git -C "$REPO_ROOT" rev-parse --short HEAD)
    log "Commit: $short ($commit)"

    # Rsync project files to remote (excludes .git, venv, __pycache__, data/)
    log "Syncing files to $vm..."
    rsync -az --delete \
        --exclude='.git' \
        --exclude='*/venv' \
        --exclude='*/__pycache__' \
        --exclude='agentic-ui/data' \
        --exclude='agentic-ui/uploads' \
        --exclude='*/.env' \
        --exclude='oauth-refresh-daemon/.env' \
        "$REPO_ROOT/" \
        "marcus@$vm:$REMOTE_DIR/"

    # Restart services
    log "Restarting services on $vm..."
    ssh "marcus@$vm" "
        cd $REMOTE_DIR/agentic-ui
        [ -d venv ] || (python3 -m venv venv && venv/bin/pip install -q -r requirements.txt)
        venv/bin/pip install -q -r requirements.txt
        systemctl --user restart $SERVICES
        systemctl --user is-active $SERVICES
    "

    # Update state.json
    update_state "$env" "$commit"

    # Commit state update
    git -C "$REPO_ROOT" add "$STATE_FILE"
    git -C "$REPO_ROOT" commit -m "deploy: $env → $short" --allow-empty

    log "Deploy to $env complete."
    show_status
}

# ── Main ─────────────────────────────────────────────────────────────────────

case "${1:-status}" in
    prod)
        deploy_to "prod" "$PROD_VM" "$PROD_BRANCH"
        ;;
    test)
        deploy_to "test" "$TEST_VM" "$DEV_BRANCH"
        ;;
    status)
        show_status
        ;;
    promote)
        # Merge dev → main and deploy to PROD
        log "Promoting TEST → PROD..."
        git -C "$REPO_ROOT" checkout "$PROD_BRANCH"
        git -C "$REPO_ROOT" merge "$DEV_BRANCH" --no-edit
        deploy_to "prod" "$PROD_VM" "$PROD_BRANCH"
        ;;
    *)
        echo "Usage: $0 [test|prod|promote|status]"
        echo "  test     — deploy dev branch to TEST (VM 105)"
        echo "  prod     — deploy main branch to PROD (VM 102)"
        echo "  promote  — merge dev → main and deploy to PROD"
        echo "  status   — show current deployment state"
        exit 1
        ;;
esac
