#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Mnemosyne OS · install / self-check script
#
# Usage:
#   ./setup.sh --check     # check the environment only, no changes made (**run this first**)
#   ./setup.sh             # full install: venv + dependencies + config template + DB init
#   ./setup.sh --start     # start the service and run a health self-check
#
# Design principles:
#   · Idempotent — safe to re-run; existing things are not overwritten
#   · Only touches the system outside of --check; --check is read-only throughout
#   · No hardcoded paths, usernames, or credentials (everything goes through variables / .env)
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
PY="${PYTHON:-python3}"
DB_NAME="${MNEMOSYNE_DB_NAME:-mnemosyne}"
DB_USER="${MNEMOSYNE_DB_USER:-mnemosyne}"
DB_HOST="${MNEMOSYNE_DB_HOST:-127.0.0.1}"
DB_PORT="${MNEMOSYNE_DB_PORT:-5432}"
HOST="${MNEMOSYNE_HOST:-127.0.0.1}"
PORT="${MNEMOSYNE_PORT:-8010}"

MODE="install"
case "${1:-}" in
  --check|-c) MODE="check" ;;
  --start|-s) MODE="start" ;;
  --help|-h) sed -n '2,14p' "$0"; exit 0 ;;
  "") ;;
  *) echo "Unknown argument: $1 (use --help for usage)" >&2; exit 2 ;;
esac

ok(){   printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad(){  printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }
step(){ printf '\n\033[1m%s\033[0m\n' "$1"; }

FAILED=0

# ── 1. Preflight checks (check mode stops here) ─────────────────────────────
step "① Environment check"
if command -v "$PY" >/dev/null 2>&1; then
  PYV="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  if "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)'; then
    ok "Python $PYV (requires ≥3.11)"
  else
    bad "Python $PYV is too old (requires ≥3.11)"; FAILED=1
  fi
else
  bad "$PY not found"; FAILED=1
fi

if command -v psql >/dev/null 2>&1; then
  ok "psql available ($(psql --version | awk '{print $3}'))"
else
  warn "psql not in PATH — database init will be skipped (a remote DB also works, just configure .env)"
fi

command -v git >/dev/null 2>&1 && ok "git available" || warn "git not in PATH (doesn't affect runtime)"

# pgvector is a hard dependency (vector retrieval)
if command -v psql >/dev/null 2>&1; then
  if PGPASSWORD="${PGPASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
       -tAc "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | grep -q 1; then
    ok "pgvector extension is ready in database $DB_NAME"
  else
    warn "Could not confirm pgvector (DB not created / extension not installed / credentials needed) — see the database section in INSTALL.md"
  fi
fi

if [ -f "$REPO_DIR/requirements.txt" ]; then
  ok "requirements.txt present ($(( $(wc -l < "$REPO_DIR/requirements.txt") )) lines)"
else
  warn "requirements.txt not found"
fi

if [ "$MODE" = "check" ]; then
  step "Check complete (no changes made)"
  [ "$FAILED" -eq 0 ] && echo "  Environment is ready to install." || { echo "  Blocking issues found, see ✗ above."; exit 1; }
  exit 0
fi

# ── 2. Virtual environment + dependencies ───────────────────────────────────
step "② Virtual environment and dependencies"
if [ -d "$VENV_DIR" ]; then
  ok "Virtual environment already exists: $VENV_DIR (skipping creation)"
else
  "$PY" -m venv "$VENV_DIR" && ok "Created virtual environment: $VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --quiet --upgrade pip && ok "pip upgraded"
if [ -f "$REPO_DIR/requirements.txt" ]; then
  python -m pip install --quiet -r "$REPO_DIR/requirements.txt" && ok "Dependencies installed"
fi

# ── 3. Configuration ─────────────────────────────────────────────────────────
step "③ Configuration"
if [ -f "$REPO_DIR/.env" ]; then
  ok ".env already exists (not overwritten, to avoid wiping your secrets)"
elif [ -f "$REPO_DIR/.env.template" ]; then
  cp "$REPO_DIR/.env.template" "$REPO_DIR/.env"
  warn "Generated .env from .env.template — **please fill in your API keys**, otherwise model calls will fail"
else
  warn "Neither .env nor .env.template found"
fi

# ── 4. Database ───────────────────────────────────────────────────────────────
step "④ Database"
if command -v psql >/dev/null 2>&1 && [ -f "$REPO_DIR/docs/schema.sql" ]; then
  if PGPASSWORD="${PGPASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
       -tAc "SELECT to_regclass('public.memories')" 2>/dev/null | grep -q memories; then
    ok "Database $DB_NAME already initialized (memories table exists, skipping schema)"
  else
    warn "Database not ready. Create it first and run: psql ... -f docs/schema.sql (or use a remote DB and just edit .env)"
  fi
else
  warn "Skipping database init (psql missing or docs/schema.sql not found)"
fi

# ── 5. Self-check ─────────────────────────────────────────────────────────────
step "⑤ Self-check"
if [ -f "$REPO_DIR/tests/run_all.py" ]; then
  (cd "$REPO_DIR" && python tests/run_all.py --quick) && ok "Quick self-check passed" || warn "Quick self-check wasn't all green (see output above)"
else
  (cd "$REPO_DIR" && python -m pytest tests/ -q) && ok "pytest passed" || warn "pytest wasn't all green (see output above)"
fi

step "Install complete"
cat <<EOF
  Next steps:
    1) Edit .env and fill in your model API key
    2) Create the database and import docs/schema.sql (if the previous step said it wasn't ready)
    3) ./setup.sh --start   start the service and run a health self-check
  Default listen address: http://$HOST:$PORT
EOF

# ── 6. Start (--start only) ───────────────────────────────────────────────────
if [ "$MODE" = "start" ]; then
  step "⑥ Start and health check"
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q mnemosyne; then
    sudo systemctl restart mnemosyne && ok "Restarted systemd service mnemosyne"
  elif [ -f "$REPO_DIR/main.py" ]; then
    nohup python "$REPO_DIR/main.py" >/tmp/mnemosyne.log 2>&1 &
    ok "Started in the background (log: /tmp/mnemosyne.log)"
  else
    warn "No entry point found (main.py / systemd service) — start it manually per INSTALL.md"
  fi
  sleep 3
  if curl -sf "http://$HOST:$PORT/api/v1/echo" >/dev/null 2>&1; then
    ok "Health check passed: http://$HOST:$PORT"
  else
    warn "Health check failed — check /tmp/mnemosyne.log or journalctl -u mnemosyne"
  fi
fi
