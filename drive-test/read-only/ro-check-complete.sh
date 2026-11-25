#!/bin/bash
# Poll the read-only run logs and hit a webhook once every listed drive has a verdict.
# Usage: SERIALS="<serial> <serial>" ./ro-check-complete.sh <webhook-url>
# Example: SERIALS="ABC123XYZ DEF456UVW" ./ro-check-complete.sh https://example.com/notify
#
# A drive is done when its log ($LOGDIR/<serial>-ro.log) has a `RO-CHECK PASSED|WARN|FAILED`
# line. The webhook fires on completion regardless of outcome; the counts go to
# $LOGDIR/ro-check-complete.log. After a successful call a sentinel file stops repeats, so it
# is safe to run from cron. Delete $LOGDIR/ro-notified.sentinel to re-arm it.

URL=${1:-}
if [ -z "$URL" ] || [ -z "${SERIALS:-}" ]; then
  echo "Usage: SERIALS=\"<serial> <serial>\" $0 <webhook-url>"
  exit 1
fi
LOGDIR=${LOGDIR:-$HOME/drive-test-logs}
SENTINEL=$LOGDIR/ro-notified.sentinel
CHECKLOG=$LOGDIR/ro-check-complete.log
mkdir -p "$LOGDIR"
[ -f "$SENTINEL" ] && exit 0

TOTAL=0 DONE=0 PASSED=0 WARNED=0 FAILED=0
for S in $SERIALS; do
  TOTAL=$((TOTAL+1))
  V=$(grep -o -E "RO-CHECK (PASSED|WARN|FAILED) $S" "$LOGDIR/$S-ro.log" 2>/dev/null | tail -1 | awk '{print $2}')
  case $V in
    PASSED) PASSED=$((PASSED+1)); DONE=$((DONE+1)) ;;
    WARN) WARNED=$((WARNED+1)); DONE=$((DONE+1)) ;;
    FAILED) FAILED=$((FAILED+1)); DONE=$((DONE+1)) ;;
  esac
done
echo "[$(date)] $PASSED passed, $WARNED warn, $FAILED failed, of $TOTAL drives" >> "$CHECKLOG"

if [ "$DONE" -eq "$TOTAL" ]; then
  HTTP_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$URL")
  echo "[$(date)] all $TOTAL finished ($PASSED passed, $WARNED warn, $FAILED failed); webhook returned HTTP $HTTP_CODE" >> "$CHECKLOG"
  [ "$HTTP_CODE" = "200" ] && touch "$SENTINEL"
fi
