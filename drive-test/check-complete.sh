#!/bin/bash
# Poll phase 2 logs and hit a webhook once every drive has a verdict.
# Usage: ./check-complete.sh <webhook-url>
# Drives checked default to sda-sdd; override with DRIVES="e f g" ./check-complete.sh <url>
# A drive is "done" when its log has PHASE 2 COMPLETE (clean) or PHASE 2 FAILED.
URL=${1:-}
if [ -z "$URL" ]; then
  echo "Usage: $0 <webhook-url>"
  exit 1
fi
DRIVES=${DRIVES:-"a b c d"}
SENTINEL=/tmp/phase2-notified.sentinel
LOG=/tmp/check-complete.log

# Already notified? Exit.
[ -f "$SENTINEL" ] && exit 0

TOTAL=0 PASSED=0 FAILED=0
for L in $DRIVES; do
  TOTAL=$((TOTAL+1))
  if grep -q 'PHASE 2 COMPLETE' /tmp/sd$L-phase2.log 2>/dev/null; then
    PASSED=$((PASSED+1))
  elif grep -q 'PHASE 2 FAILED' /tmp/sd$L-phase2.log 2>/dev/null; then
    FAILED=$((FAILED+1))
  fi
done

echo "[$(date)] $PASSED passed, $FAILED failed, of $TOTAL drives" >> $LOG

if [ $((PASSED+FAILED)) -eq "$TOTAL" ]; then
  HTTP_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$URL")
  echo "[$(date)] All $TOTAL finished ($PASSED passed, $FAILED failed). Webhook returned HTTP $HTTP_CODE" >> $LOG
  if [ "$HTTP_CODE" = "200" ]; then
    touch "$SENTINEL"
  fi
fi
