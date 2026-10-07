#!/bin/bash
# Mnemosyne post-release version-consistency scan v2
# Usage: bash scripts/version-scan.sh [old_version] [new_version]
# Distinguishes current-version references from historical-record/feature-tag mentions

set -euo pipefail

OLD="${1:-}"
NEW="${2:-}"

if [ -z "$OLD" ] || [ -z "$NEW" ]; then
  OLD=$(git tag --sort=-version:refname 2>/dev/null | head -2 | tail -1 | sed 's/^v//')
  NEW=$(git tag --sort=-version:refname 2>/dev/null | head -1 | sed 's/^v//')
  if [ -z "$OLD" ] || [ -z "$NEW" ]; then
    echo "Usage: $0 <old_version> <new_version>"
    exit 1
  fi
fi

echo "🔍 Version scan: $OLD → $NEW"
FAILS=0

# Helper: count leftover non-functional-tag occurrences
count_non_func() {
  local file=$1 total=0 func=0
  total=$(grep -cF "$OLD" "$file" 2>/dev/null) || total=0
  func=$(grep -cE "\(v?$OLD\)" "$file" 2>/dev/null) || func=0
  echo $((total - func))
}

# ── 1. Key fields (must have zero leftovers) ──
echo ""
echo "=== Key version fields ==="
check_key() {
  local label=$1 file=$2 pattern=$3
  local actual
  actual=$(grep -oP "$pattern" "$file" 2>/dev/null | head -1 || echo "MISSING")
  if [ "$actual" != "$NEW" ] && [ "$actual" != "v$NEW" ]; then
    echo "  ❌ $label: $actual (expected $NEW)"
    FAILS=$((FAILS + 1))
  else
    echo "  ✅ $label: $actual"
  fi
}

check_key "VERSION"     "VERSION"           '.*'
check_key "README badge" "README.md"        '(?<=version-)[\d.]+'
check_key "AGENTS.md"   "AGENTS.md"         '(?<=\*\*Current version\*\*: v)[\d.]+'
check_key "CHANGELOG latest" "CHANGELOG.md" '(?<=^## release · v)[\d.]+'
check_key "README version table"  "README.md" '(?<=\| \[v)[\d.]+(?=\]\()'
check_key "ROADMAP"       "ROADMAP.md"       '(?<=^> v)[\d.]+'
# main.py: echo/capabilities now read the VERSION file at runtime → the only hardcoded spot is the FastAPI title
check_key "main.py title" "main.py"          '(?<=title="Mnemosyne OS v)[\d.]+(?= )'

# ── 2. Skill docs (only check for leftover non-functional tags) ──
echo ""
echo "=== Skill docs ==="
SKILL_DIR="$HOME/.hermes/skills"
for skill in $(grep -rlF "$OLD" "$SKILL_DIR" --include='SKILL.md' 2>/dev/null | grep -v 'references/' | grep -v '.archive/' | grep -v 'CHANGELOG'); do
  nf=$(count_non_func "$skill")
  if [ "$nf" -gt 0 ] 2>/dev/null; then
    sname=$(basename $(dirname "$skill"))
    echo "  ❌ $sname: $nf leftover non-functional tag(s)"
    FAILS=$((FAILS + 1))
  fi
done
# If nothing is left over, show all-clear
if [ "$FAILS" -eq 0 ] || ! grep -rlF "$OLD" "$SKILL_DIR" --include='SKILL.md' 2>/dev/null | grep -qv 'references/\|.archive/\|CHANGELOG'; then
  echo "  ✅ All clear"
fi

# ── 3. Hermes Memory ──
echo ""
echo "=== Hermes Memory ==="
if grep -qF "$OLD" "$HOME/.hermes/memories/MEMORY.md" 2>/dev/null; then
  echo "  ❌ MEMORY.md still references $OLD"
  FAILS=$((FAILS + 1))
else
  echo "  ✅ MEMORY.md"
fi

# ── 4. Production running version ──
echo ""
echo "=== Production running version ==="
prd_ver=$(curl -s --max-time 5 http://127.0.0.1:18010/api/v1/echo 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "unreachable")
if [ "$prd_ver" = "$NEW" ]; then
  echo "  ✅ Production: $prd_ver"
else
  echo "  ❌ Production: $prd_ver (expected $NEW)"
  FAILS=$((FAILS + 1))
fi

# ── 5. Workspace ──
echo ""
echo "=== Workspace PROGRESS ==="
ws_file="/opt/data/workspace/记忆宫殿/PROGRESS.md"
if [ -f "$ws_file" ]; then
  ws_ver=$(grep '当前版本' "$ws_file" | grep -oP 'v?[\d.]+' | head -1 | sed 's/^v//')
  if [ "$ws_ver" = "$NEW" ]; then
    echo "  ✅ v$ws_ver"
  else
    echo "  ❌ v$ws_ver (expected $NEW)"
    FAILS=$((FAILS + 1))
  fi
else
  echo "  ⚠️ Workspace unreachable"
fi

# ── Summary ──
echo ""
echo "════════════════════════════"
if [ "$FAILS" -eq 0 ]; then
  echo "✅ Version scan: all passed"
  exit 0
else
  echo "❌ Found $FAILS inconsistency/inconsistencies"
  exit 1
fi
