#!/bin/bash
# Wait for one drive's queue-badblocks.sh run to finish, then run phase 2 (ZFS + f3 +
# scrub + SMART long) on it, but only if badblocks PASSED. Meant to be started in a
# detached screen session next to the drive's badblocks session.
# Usage: ./queue-phase2.sh <serial>
# Example: screen -dmS p2-ABC123XYZ ./queue-phase2.sh ABC123XYZ
#
# WARNING: phase 2 destroys all data on the drive (it creates a ZFS pool on it).
#
# Why a separate waiter instead of chaining inside queue-badblocks.sh: badblocks runs
# take days and may be stopped or restarted by hand; this script only looks at the
# verdict line in the log, so it works for any badblocks run, and a stopped run
# (no verdict) simply keeps it waiting.
# Env overrides (for testing): LOGDIR, PHASE2_SCRIPT, POLL_SECONDS.

SERIAL=${1:-}
if [ -z "$SERIAL" ]; then
  echo "Usage: $0 <serial>"
  exit 1
fi

LOGDIR=${LOGDIR:-$HOME/drive-test-logs}
PHASE2=${PHASE2_SCRIPT:-$(dirname "$(readlink -f "$0")")/drive-test-phase2.sh}
POLL=${POLL_SECONDS:-300}
BBLOG=$LOGDIR/$SERIAL-badblocks.log
LOG=$LOGDIR/$SERIAL-pipeline.log
mkdir -p "$LOGDIR"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "WAITING for badblocks verdict on $SERIAL ($BBLOG)"
while :; do
  if grep -q "BADBLOCKS PASSED $SERIAL" "$BBLOG" 2>/dev/null; then break; fi
  if grep -qE "BADBLOCKS FAILED $SERIAL|SKIPPED:|ABORT:" "$BBLOG" 2>/dev/null; then
    log "SKIPPED phase 2: badblocks did not pass: $(grep -E 'BADBLOCKS FAILED|SKIPPED:|ABORT:' "$BBLOG" | tail -1)"
    exit 2
  fi
  sleep "$POLL"
done

log "badblocks PASSED; starting phase 2 on $SERIAL"
bash "$PHASE2" "$SERIAL"
# drive-test-phase2.sh always exits 0; its verdict is the last line in its log.
P2LOG=/tmp/$SERIAL-phase2.log
[ -f "$P2LOG" ] && cp "$P2LOG" "$LOGDIR/$SERIAL-phase2.log"
VERDICT=$(grep -E 'PHASE 2 (COMPLETE|FAILED)' "$P2LOG" 2>/dev/null | tail -1)
log "${VERDICT:-PHASE 2 produced no verdict (see $P2LOG)}"
