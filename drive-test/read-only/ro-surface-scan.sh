#!/bin/bash
# Read every sector of the drive once and report unreadable blocks. Uses badblocks in its
# default read-only mode; the write (-w) and non-destructive read-write (-n) modes are
# never used. Unlike a file copy this also covers free space and unallocated areas.
# Usage: ./ro-surface-scan.sh <serial>
# Example: screen -dmS scan-ABC123XYZ ./ro-surface-scan.sh ABC123XYZ
#
# Takes hours on a multi-TB disk. If you are already copying the drive's files off with
# ro-copy-verify.sh the used area has been read once; this adds the unused remainder.
# Exit: 0 no unreadable blocks, 1 unreadable blocks or badblocks error.

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

STEP=surface
ro_init "${1:-}"

# Read-only by construction: the argument list has no -w or -n, and is asserted below.
ARGS=(-b 4096 -c 65535 -sv)
for a in "${ARGS[@]}"; do
  case $a in -w | -n) log "ABORT: refusing a write-capable badblocks mode"; exit 1 ;; esac
done

BADLIST=$SNAPDIR/surface-badblocks.txt
: > "$BADLIST"
log "reading all of $DEV ($SERIAL) with badblocks (read-only)"
$SUDO badblocks "${ARGS[@]}" -o "$BADLIST" "$ID" 2>&1 | tr '\b' '\n' | tee -a "$LOG" > /dev/null
RC=${PIPESTATUS[0]}

# -o writes one block number per unreadable block; any line means a bad block.
BAD=$(grep -c -E '^[0-9]+$' "$BADLIST")
if [ "$RC" -eq 0 ] && [ "$BAD" -eq 0 ]; then
  log "PASSED: 0 unreadable blocks"
  exit $RC_PASS
fi
log "FAILED: badblocks rc=$RC, unreadable blocks listed=$BAD (see $BADLIST)"
exit $RC_FAIL
