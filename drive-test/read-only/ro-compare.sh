#!/bin/bash
# Take a second SMART snapshot and compare it with the baseline, plus scan the kernel log
# for I/O errors on the drive since the run started. This is the check that tells you
# whether the other tests stressed a weak drive: it fails if bad-sector counters rose.
# Usage: ./ro-compare.sh <serial>
# Example: ./ro-compare.sh ABC123XYZ
#
# Needs a baseline from ro-baseline.sh. Writes the "after" snapshot next to it.
# Exit: 0 unchanged, 1 failure indicators rose or kernel I/O errors on the drive,
#       2 WARN (softer indicators rose, or link resets seen), 3 no baseline / no SMART.

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

STEP=compare
ro_init "${1:-}"
BASE=$SNAPDIR/baseline.attrs
if [ ! -f "$BASE" ] || [ "$(attr_get "$BASE" smart)" != ok ]; then
  log "SKIPPED: no usable baseline snapshot (run ro-baseline.sh first, or the drive has no SMART)"
  exit $RC_SKIP
fi

"$DIR/ro-baseline.sh" "$SERIAL" after > /dev/null
AFTER=$SNAPDIR/after.attrs
FAILS=0 WARNS=0

# rose <key>: true when the numeric value grew since the baseline.
rose() {
  local b a
  b=$(attr_get "$BASE" "$1"); a=$(attr_get "$AFTER" "$1")
  [[ $b =~ ^[0-9]+$ && $a =~ ^[0-9]+$ ]] && [ "$a" -gt "$b" ]
}

if [ "$(attr_get "$AFTER" health)" != PASSED ]; then
  log "FAILED: SMART health is now $(attr_get "$AFTER" health)"; FAILS=$((FAILS+1))
fi
# Counters that mean sectors went bad (or the drive cannot read them) during the run.
for k in attr_5 attr_197 attr_198 attr_187 media_errors; do
  if rose "$k"; then
    log "FAILED: $k rose from $(attr_get "$BASE" "$k") to $(attr_get "$AFTER" "$k")"; FAILS=$((FAILS+1))
  fi
done
if rose errlog; then
  log "FAILED: ATA error log grew from $(attr_get "$BASE" errlog) to $(attr_get "$AFTER" errlog) entries"; FAILS=$((FAILS+1))
fi
# Softer signals: CRC errors point at the cable/port more than the disk; the others are
# informational but worth a look.
for k in attr_199 attr_188 attr_196; do
  if rose "$k"; then
    log "WARN: $k rose from $(attr_get "$BASE" "$k") to $(attr_get "$AFTER" "$k") (cable/port or timeouts rather than surface damage)"; WARNS=$((WARNS+1))
  fi
done

# Kernel log since the run started. Errors naming this device are failures; ATA link resets
# cannot be tied to a device name here, so they are only a warning.
SINCE=$(cat "$SNAPDIR/started" 2>/dev/null)
if [ -n "$SINCE" ]; then
  KLOG=$($SUDO journalctl -k --no-pager --since "@$SINCE" 2>/dev/null)
  IOERR=$(echo "$KLOG" | grep -E "(I/O error|Buffer I/O error|blk_update_request).*\b$DEVNAME\b|\b$DEVNAME\b.*(I/O error|timeout)" | head -5)
  RESETS=$(echo "$KLOG" | grep -E 'ata[0-9]+(\.[0-9]+)?: .*(hard resetting link|failed command|SError)' | head -5)
  if [ -n "$IOERR" ]; then
    log "FAILED: kernel reported I/O errors on $DEVNAME since the run started:"; echo "$IOERR" | tee -a "$LOG"; FAILS=$((FAILS+1))
  fi
  if [ -n "$RESETS" ]; then
    log "WARN: ATA link resets/failed commands in the kernel log since the run started (may be this drive's port):"; echo "$RESETS" | tee -a "$LOG"; WARNS=$((WARNS+1))
  fi
fi

if [ $FAILS -gt 0 ]; then log "FAILED: $FAILS indicator(s) got worse during the run"; exit $RC_FAIL; fi
if [ $WARNS -gt 0 ]; then log "WARN: $WARNS soft indicator(s) changed"; exit $RC_WARN; fi
log "PASSED: SMART counters and kernel log unchanged since the baseline"
exit $RC_PASS
