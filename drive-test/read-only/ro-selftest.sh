#!/bin/bash
# Run the drive's own firmware self-test and report the result. The drive reads
# itself; nothing is written to the user data area.
# Usage: ./ro-selftest.sh <serial> [short|long]      (default: short)
# Example: screen -dmS st-ABC123XYZ ./ro-selftest.sh ABC123XYZ long
#
# A short test takes about two minutes; a long test reads the whole surface (roughly
# 2-8 hours on a 2-4 TB disk). Safe to run while the drive's filesystems are mounted
# read-only. Env: MAX_WAIT_HOURS (default 24) bounds the polling.
#
# Exit: 0 passed, 1 failed/aborted, 3 skipped (no SMART, NVMe, or test would not start).

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

KIND=${2:-short}
STEP=selftest-$KIND
ro_init "${1:-}"
case $KIND in short | long) ;; *) echo "Usage: $0 <serial> [short|long]"; exit 1 ;; esac

if ! detect_smart; then
  log "SKIPPED: no SMART available"
  exit $RC_SKIP
fi
if is_nvme; then
  log "SKIPPED: NVMe self-test polling is not implemented; rely on the SMART health log"
  exit $RC_SKIP
fi

in_progress() { smart -c 2>/dev/null | grep -q 'Self-test routine in progress'; }

if in_progress; then
  log "a self-test is already running; waiting for it rather than starting another"
else
  START=$(smart -t "$KIND" 2>&1)
  if ! echo "$START" | grep -q -E 'Self-testing has begun|Test will complete|has begun'; then
    log "SKIPPED: the drive would not start a $KIND self-test: $(echo "$START" | tail -2 | tr '\n' ' ')"
    exit $RC_SKIP
  fi
  log "started $KIND self-test on $DEV ($SERIAL)"
fi

# A drive that was asleep can take a few seconds before it reports "in progress".
sleep "${START_GRACE_SECONDS:-5}"
DEADLINE=$(( $(date +%s) + ${MAX_WAIT_HOURS:-24} * 3600 ))
while in_progress; do
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    log "FAILED: self-test still running after ${MAX_WAIT_HOURS:-24} h"
    exit $RC_FAIL
  fi
  sleep "$POLL_SECONDS"
done

# The newest log entry is the test that just ended.
LINE=$(smart -l selftest 2>/dev/null | grep -E '^# ?1 ' | head -1)
if echo "$LINE" | grep -q 'Completed without error'; then
  log "PASSED: $LINE"
  exit $RC_PASS
fi
log "FAILED: $LINE"
exit $RC_FAIL
