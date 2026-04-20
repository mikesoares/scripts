#!/bin/bash
# Poll phase 2 completion and hit webhook when all 4 drives done
# Usage: ./check-complete.sh <webhook-url>
URL=${1:-}
if [ -z "$URL" ]; then
  echo "Usage: $0 <webhook-url>"
  exit 1
fi
SENTINEL=/tmp/phase2-notified.sentinel
LOG=/tmp/check-complete.log

# Already notified? Exit.
[ -f "$SENTINEL" ] && exit 0

# Count completed drives
COMPLETE=0
for L in a b c d; do
  if grep -q 'PHASE 2 COMPLETE' /tmp/sd$L-phase2.log 2>/dev/null; then
    COMPLETE=$((COMPLETE+1))
  fi
done

echo "[$(date)] $COMPLETE/4 drives complete" >> $LOG

if [ "$COMPLETE" -eq 4 ]; then
  HTTP_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$URL")
  echo "[$(date)] All 4 complete. Webhook returned HTTP $HTTP_CODE" >> $LOG
  if [ "$HTTP_CODE" = "200" ]; then
    touch "$SENTINEL"
  fi
fi
