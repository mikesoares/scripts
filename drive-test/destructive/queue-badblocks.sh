#!/bin/bash
# Wait for one drive's SMART long self-test to finish and pass, then run a single
# destructive badblocks write+read pass (pattern 0xaa) on it. Meant to be started
# in a detached screen session so several drives can queue at once.
# Usage: [BB_PATTERN=0xaa] ./queue-badblocks.sh <serial>
# Example: screen -dmS bb-ABC123XYZ ./queue-badblocks.sh ABC123XYZ
#
# WARNING: destroys all data on the drive once the gate passes.
#
# Why serial + /dev/disk/by-id: /dev/sdX letters can change after a controller or
# cable swap; the by-id path follows the physical drive, so a logged result always
# belongs to the right disk.
# Pattern: by default badblocks' full 4-pattern run (0xaa, 0x55, 0xff, 0x00; each a
# write pass plus a read-back pass), the forum burn-in methodology; it takes days on a
# multi-TB drive. Set BB_PATTERN=0xaa for a single-pattern pass (writes and reads every
# sector once; fits in an overnight window).

SERIAL=${1:-}
if [ -z "$SERIAL" ]; then
  echo "Usage: $0 <serial>"
  exit 1
fi

LOGDIR=$HOME/drive-test-logs
LOG=$LOGDIR/$SERIAL-badblocks.log
mkdir -p "$LOGDIR"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

ID=$(ls /dev/disk/by-id/ata-*_"$SERIAL" 2>/dev/null | grep -v -- '-part' | head -1)
if [ -z "$ID" ]; then
  log "ABORT: no drive with serial $SERIAL under /dev/disk/by-id"
  exit 1
fi
DEV=$(readlink -f "$ID")

# Refuse anything with a mounted filesystem (e.g. the boot disk).
if [ -n "$(lsblk -no MOUNTPOINTS "$DEV" | tr -d '[:space:]')" ]; then
  log "ABORT: $DEV ($SERIAL) has a mounted filesystem"
  exit 1
fi

log "QUEUED $SERIAL as $DEV ($ID); waiting for SMART self-test to finish"
while sudo smartctl -c "$DEV" | grep -q 'Self-test routine in progress'; do
  sleep 120
done
# The device letter could in theory change while waiting; re-resolve it.
DEV=$(readlink -f "$ID")

# Gate: newest self-test is an extended one that completed clean, health PASSED,
# and no reallocated/pending/offline-uncorrectable sectors.
SELFTEST=$(sudo smartctl -l selftest "$DEV" | grep -E '^# ?1 ')
BAD=$(sudo smartctl -A "$DEV" | awk '$1==5||$1==197||$1==198 { if ($10+0 != 0) print $1"="$10 }')
if ! echo "$SELFTEST" | grep -q 'Extended.*Completed without error'; then
  log "SKIPPED: long self-test did not complete clean: $SELFTEST"; exit 2
fi
if ! sudo smartctl -H "$DEV" | grep -q PASSED; then
  log "SKIPPED: SMART health not PASSED"; exit 2
fi
if [ -n "$BAD" ]; then
  log "SKIPPED: bad-sector attributes non-zero: $BAD"; exit 2
fi

log "GATE PASSED: starting badblocks on $DEV ($SERIAL), patterns: ${BB_PATTERN:-all four}"
PATTERN_ARGS=()
[ -n "${BB_PATTERN:-}" ] && PATTERN_ARGS=(-t "$BB_PATTERN")
sudo badblocks -b 4096 -c 65535 -wsv "${PATTERN_ARGS[@]}" "$ID" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}

# badblocks prints its bad-block count as it goes; any listed block (a bare number
# line) or a non-zero exit is a failure. Also re-check SMART afterwards.
BADBLOCKS=$(tr '\b' '\n' < "$LOG" | grep -E '^[0-9]+$' | wc -l)
AFTER=$(sudo smartctl -A "$DEV" | awk '$1==5||$1==197||$1==198 { if ($10+0 != 0) print $1"="$10 }')
if [ "$RC" -eq 0 ] && [ "$BADBLOCKS" -eq 0 ] && [ -z "$AFTER" ]; then
  log "BADBLOCKS PASSED $SERIAL (0 bad blocks, SMART 5/197/198 all 0)"
else
  log "BADBLOCKS FAILED $SERIAL (rc=$RC, bad blocks listed=$BADBLOCKS, SMART after: ${AFTER:-clean})"
fi
