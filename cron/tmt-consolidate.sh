#!/bin/bash
# Mnemosyne TMT distillation cron job
# daily=1am every day, weekly=Sunday 1:30am, monthly=2am on the 1st
# v6.5 fix: monthly month uses $((10#$(date +%m))) to strip the leading zero (08 is invalid JSON)

API="http://127.0.0.1:8010"
LOG="/var/log/tmt-consolidate.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }

case "${1:-daily}" in
  daily)
    log "[daily] Starting session consolidation..."
    curl -s -X POST "$API/api/v1/tmt/consolidate/session"       -H 'Content-Type: application/json'       -d '{"user_id":"default"}' >> "$LOG" 2>&1
    log "[daily] Starting daily consolidation..."
    curl -s -X POST "$API/api/v1/tmt/consolidate/daily"       -H 'Content-Type: application/json'       -d '{"user_id":"default"}' >> "$LOG" 2>&1
    log "[daily] Done."
    ;;
  weekly)
    log "[weekly] Starting..."
    curl -s -X POST "$API/api/v1/tmt/consolidate/weekly"       -H 'Content-Type: application/json'       -d '{"user_id":"default"}' >> "$LOG" 2>&1
    log "[weekly] Done."
    ;;
  monthly)
    Y=$(date +%Y)
    M=$((10#$(date +%m)))
    log "[monthly] Starting for $Y-$M..."
    curl -s -X POST "$API/api/v1/tmt/consolidate/monthly"       -H 'Content-Type: application/json'       -d "{\"user_id\":\"default\",\"year\":$Y,\"month\":$M}" >> "$LOG" 2>&1
    log "[monthly] Done."
    ;;
esac
