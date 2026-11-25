#!/bin/bash
# Run read-only drive checks as a queue: each step starts only if the previous one did not
# fail. Meant for a detached screen session. Never writes to the drive.
# Usage: ./ro-queue.sh <serial> <step> [<step> ...]     steps: baseline short long surface compare
# Example: screen -dmS ro-ABC123XYZ ./ro-queue.sh ABC123XYZ baseline short long surface compare
#
# After each step: PASSED (0) and WARN (2) continue the queue; FAILED (1) or SKIPPED (3, the
# step could not run, so what follows would not be "after a pass") stop it.
# Verdict, last line of $LOGDIR/<serial>-ro.log: RO-CHECK PASSED|WARN|FAILED|INCOMPLETE <serial>
#   INCOMPLETE = stopped because a step was skipped.
DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"
STEP=queue
ro_init "${1:-}"; shift
[ $# -gt 0 ] || { echo "Usage: $0 <serial> <step>..."; exit 1; }

WARNED=() 
log "=== queue on $DEV ($SERIAL): $* ==="
for s in "$@"; do
  case $s in
    baseline) CMD=(ro-baseline.sh) ;;
    short)    CMD=(ro-selftest.sh short) ;;
    long)     CMD=(ro-selftest.sh long) ;;
    surface)  CMD=(ro-surface-scan.sh) ;;
    compare)  CMD=(ro-compare.sh) ;;
    *) log "unknown step: $s"; exit 1 ;;
  esac
  log "--- $s ---"
  "$DIR/${CMD[0]}" "$SERIAL" "${CMD[@]:1}"
  rc=$?
  case $rc in
    0) ;;
    2) WARNED+=("$s") ;;
    1) log "step $s FAILED; queue stopped"; log "RO-CHECK FAILED $SERIAL"; exit $RC_FAIL ;;
    *) log "step $s was skipped (could not run); queue stopped"; log "RO-CHECK INCOMPLETE $SERIAL"; exit $RC_SKIP ;;
  esac
done
if [ ${#WARNED[@]} -gt 0 ]; then log "steps with warnings: ${WARNED[*]}"; log "RO-CHECK WARN $SERIAL"; exit $RC_WARN; fi
log "RO-CHECK PASSED $SERIAL"
