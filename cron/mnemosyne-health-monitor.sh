#!/bin/bash
# Mnemosyne enhanced health check v5.5.1
# Covers: API reachability + TMT distillation health + system resources
# Alerting: via the production security-guard Hermes persona → WeChat
API_BASE="http://127.0.0.1:8010"
LOG="/var/log/mnemosyne-health.log"
ALERT_FLAG="/tmp/mnemosyne_alert"
USER_ID="default"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

# ── 1. Basic API health ──
api_code=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" "$API_BASE/api/v1/echo" 2>/dev/null)
version=$(curl -s --max-time 10 "$API_BASE/api/v1/echo" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('version','?'))" 2>/dev/null)

# ── 2. TMT distillation health ──
tmt_tree=$(curl -s --max-time 10 "$API_BASE/api/v1/tmt/tree/$USER_ID" 2>/dev/null)
l3_count=$(echo "$tmt_tree" | python3 -c "import sys,json;print(json.load(sys.stdin)['levels']['L3']['count'])" 2>/dev/null || echo "0")

# Last L3 date (query PostgreSQL directly)
last_l3_date=$(sudo -u postgres psql -d mnemosyne -t -c "SELECT date FROM ag_catalog.tmt_daily WHERE user_id='default' ORDER BY date DESC LIMIT 1;" 2>/dev/null | xargs)

# ── 3. Memory store status ──
stats=$(curl -s --max-time 10 "$API_BASE/api/v1/memories/stats?user_id=$USER_ID" 2>/dev/null)
total=$(echo "$stats" | python3 -c "import sys,json;print(json.load(sys.stdin)['total'])" 2>/dev/null || echo "?")

# ── 4. System resources ──
mem_used=$(free -h | awk '/Mem:/{print $3}')
disk_used=$(df -h / | awk 'NR==2{print $5}')
load=$(uptime | awk -F'load average:' '{print $2}' | xargs)

# ── 4.5 Backup freshness (added in v6.2: guards against silent backup failures losing memories) ──
BK_DIR="/path/to/your/backups"
latest_backup=$(ls -t "$BK_DIR"/mnemosyne-*.dump 2>/dev/null | head -1)
if [ -n "$latest_backup" ]; then
  backup_days=$(( ($(date +%s) - $(date -r "$latest_backup" +%s 2>/dev/null || echo 0)) / 86400 ))
else
  backup_days=999
fi

# ── Logging ──
{
  log "=== Mnemosyne Health v5.5.1 ==="
  log "API: $api_code | Version: $version"
  log "TMT: L3=$l3_count | Last L3: $last_l3_date"
  log "Memories: $total total"
  log "System: mem=$mem_used disk=$disk_used load=$load"
  log "Backup: latest=$latest_backup days=$backup_days"
} >> "$LOG"

# ── Alert evaluation ──
ALERT=""
ALERT_ITEMS=""

if [ "$api_code" != "200" ]; then
  ALERT="true"
  ALERT_ITEMS="$ALERT_ITEMS\n  ❌ API unreachable (HTTP $api_code)"
fi

# TMT distillation alert: no new L3 for over 2 days
if [ "$last_l3_date" != "unknown" ] && [ -n "$last_l3_date" ]; then
  days_since=$(( ($(date +%s) - $(date -d "$last_l3_date" +%s 2>/dev/null || echo 0)) / 86400 ))
  if [ "$days_since" -gt 2 ] 2>/dev/null; then
    ALERT="true"
    ALERT_ITEMS="$ALERT_ITEMS\n  ⚠️ TMT L3 distillation stalled for ${days_since} days (last: $last_l3_date)"
  fi
fi

# Disk alert
disk_pct=$(echo "$disk_used" | tr -d '%')
if [ "$disk_pct" -gt 85 ] 2>/dev/null; then
  ALERT="true"
  ALERT_ITEMS="$ALERT_ITEMS\n  ⚠️ Disk usage $disk_used"
fi

# Backup freshness alert: no new backup in >10 days (backup.sh runs every Sunday)
if [ "$backup_days" -gt 10 ]; then
  ALERT="true"
  if [ "$backup_days" -eq 999 ]; then
    ALERT_ITEMS="$ALERT_ITEMS\n  ❌ No memory backup file found"
  else
    ALERT_ITEMS="$ALERT_ITEMS\n  ⚠️ Memory backup is ${backup_days} days stale (latest: $(basename "$latest_backup"))"
  fi
fi

# ── Alert trigger ──
if [ -n "$ALERT" ]; then
  alert_msg="🚨 Mnemosyne health alert $(date '+%m-%d %H:%M')\n$ALERT_ITEMS"
  echo -e "$alert_msg" > "$ALERT_FLAG"
  echo -e "$alert_msg" >> "$LOG"

  # Send a WeChat notification via Hermes security-guard
  # Condition: Hermes gateway is running and has a WeChat channel
  if systemctl --user is-active hermes-gateway 2>/dev/null | grep -q active; then
    export PATH="$HOME/.hermes/hermes-agent/venv/bin:$PATH"
    hermes chat -q "发送健康告警到微信：$alert_msg" -p security-guard --provider deepseek 2>> "$LOG" &
  fi
else
  rm -f "$ALERT_FLAG"
fi

log "OK"
