#!/bin/bash
# Run the whole read-only suite against one drive and end with a single verdict line.
# Usage: ./ro-run.sh <serial> [--dest DIR] [--long] [--checksum] [--loop] [--no-surface]
# Example: screen -dmS ro-ABC123XYZ ./ro-run.sh ABC123XYZ --dest /mnt/backup --long
#
# Steps, in order (each is also a standalone script):
#   1. ro-baseline      SMART snapshot (the reference for step 7)
#   2. ro-copy-verify   only with --dest; for a SUSPECT drive it runs here, before any
#                       long test, so the data is rescued with the least extra wear
#   3. ro-selftest short
#   4. ro-fscheck       no-write filesystem checks
#   5. ro-copy-verify   with --dest, for a drive that was not suspect
#   6. ro-selftest long only with --long; hours
#   7. ro-surface-scan  only when there is no --dest (a copy already read the data) and
#                       --no-surface was not given; hours
#   8. ro-compare       SMART and kernel-log deltas against the baseline
# Mounts: filesystems you want copied must already be mounted read-only. The run never
# mounts, unmounts, writes to, or repairs anything on the drive.
#
# Verdict (last line of $LOGDIR/<serial>-ro.log):
#   RO-CHECK PASSED <serial>   every step that ran passed
#   RO-CHECK WARN <serial>     nothing failed, but something was skipped or flagged
#   RO-CHECK FAILED <serial>   at least one step failed; the reasons are listed above it
# Exit code: 0 PASSED, 2 WARN, 1 FAILED. Steps are never aborted by an earlier failure,
# except that a failed baseline is still followed by the remaining checks.

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

STEP=run
SERIAL_ARG=${1:-}
shift 2>/dev/null
DEST="" LONG=0 SURFACE=1 PASS_ARGS=()
while [ $# -gt 0 ]; do
  case $1 in
    --dest) DEST=${2:-}; shift ;;
    --long) LONG=1 ;;
    --checksum | --loop) PASS_ARGS+=("$1") ;;
    --no-surface) SURFACE=0 ;;
    *) echo "unknown option: $1"; echo "Usage: $0 <serial> [--dest DIR] [--long] [--checksum] [--loop] [--no-surface]"; exit 1 ;;
  esac
  shift
done
ro_init "$SERIAL_ARG"

FAILED=() WARNED=()
# step <label> <script> [args...]: run one step and file its result.
step() {
  local label=$1; shift
  log "--- $label ---"
  "$DIR/$1" "$SERIAL" "${@:2}"
  case $? in
    0) ;;
    1) FAILED+=("$label") ;;
    *) WARNED+=("$label") ;;   # 2 = WARN, 3 = SKIPPED: both are caveats, not failures
  esac
}
copy_step() {
  if [ -n "$DEST" ]; then
    step copy ro-copy-verify.sh "$DEST" "${PASS_ARGS[@]}"
  else
    log "copy skipped (no --dest given)"
  fi
}
fs_args=(); [[ " ${PASS_ARGS[*]} " == *" --loop "* ]] && fs_args=(--loop)

log "=== read-only run on $DEV ($SERIAL) ==="
step baseline ro-baseline.sh
SUSPECT=$(attr_get "$SNAPDIR/baseline.attrs" suspect)
COPIED=0
if [ "$SUSPECT" = yes ] && [ -n "$DEST" ]; then
  log "suspect drive: copying before any long test"
  copy_step; COPIED=1
fi
step selftest-short ro-selftest.sh short
step fscheck ro-fscheck.sh "${fs_args[@]}"
if [ $COPIED = 0 ]; then copy_step; COPIED=1; fi
[ $LONG = 1 ] && step selftest-long ro-selftest.sh long
if [ -z "$DEST" ] && [ $SURFACE = 1 ]; then
  step surface ro-surface-scan.sh
fi
step compare ro-compare.sh

if [ ${#FAILED[@]} -gt 0 ]; then
  log "failed steps: ${FAILED[*]}"
  [ ${#WARNED[@]} -gt 0 ] && log "also flagged: ${WARNED[*]}"
  log "RO-CHECK FAILED $SERIAL"
  exit $RC_FAIL
elif [ ${#WARNED[@]} -gt 0 ]; then
  log "flagged or skipped steps: ${WARNED[*]}"
  log "RO-CHECK WARN $SERIAL"
  exit $RC_WARN
fi
log "RO-CHECK PASSED $SERIAL"
exit $RC_PASS
