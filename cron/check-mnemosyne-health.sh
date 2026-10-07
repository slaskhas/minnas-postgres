#!/bin/bash
# Mnemosyne health check v5.2 — personal-edition monitoring
API_BASE="http://127.0.0.1:8010"
LOG="/var/log/mnemosyne-health.log"
FAIL_COUNT_FILE="/tmp/mnemosyne_fail_count"

# 1. Basic health
api_code=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" "$API_BASE/api/v1/echo" 2>/dev/null)

# 2. Search reachability
search_code=$(curl -s --max-time 10 -X POST "$API_BASE/api/v1/memories/search" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"health_check\",\"user_id\":\"default\",\"top_k\":1}" \
  -o /dev/null -w "%{http_code}" 2>/dev/null)

echo "$(date "+%Y-%m-%d %H:%M:%S") api=$api_code search=$search_code" >> $LOG

if [ "$api_code" != "200" ] || [ "$search_code" != "200" ]; then
  # Failure: bump the counter + run deep diagnostics
  count=$(cat $FAIL_COUNT_FILE 2>/dev/null || echo 0)
  count=$((count + 1))
  echo $count > $FAIL_COUNT_FILE

  echo "=== FAIL #$count $(date) ===" >> $LOG
  echo "  api=$api_code search=$search_code" >> $LOG
  echo "  --- system ---" >> $LOG
  uptime >> $LOG
  systemctl is-active mnemosyne postgresql 2>&1 >> $LOG
  echo "  --- memory ---" >> $LOG
  free -h | head -2 >> $LOG
  echo "  --- disk ---" >> $LOG
  df -h / | tail -1 >> $LOG
  echo "  --- process ---" >> $LOG
  ps aux | grep -E "uvicorn|mnemosyne" | grep -v grep | head -3 >> $LOG
else
  # Recovered: reset the counter
  rm -f $FAIL_COUNT_FILE
fi
