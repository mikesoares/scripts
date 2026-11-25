#!/bin/bash
# Copy the drive's data to another location and verify the copy. Reading every file is the
# most realistic test that the data is still readable, and it leaves you with a backup.
# Usage: ./ro-copy-verify.sh <serial> <dest-dir> [--checksum] [--loop]
# Example: ./ro-copy-verify.sh ABC123XYZ /mnt/backup --checksum
#
# Only filesystems that are ALREADY MOUNTED READ-ONLY are read (this script never mounts
# anything and never reads through a read-write mount). Unlock/mount encrypted volumes
# yourself first; the suite never handles passwords.
#   normal drive    rsync of each mount to <dest>/<serial>/<mount name>/, then a verify
#                   pass (size+mtime compare; --checksum re-reads both sides in full)
#   suspect drive   if the last baseline flagged the drive (pending sectors, error log
#                   entries, failed health) and `ddrescue` is installed, each partition is
#                   imaged to <dest>/<serial>/<partition>.img with a mapfile instead, which
#                   retries bad areas gently and records what could not be read.
# --loop   attach a read-only 4096-byte-sector loop device if the drive's partitions are
#          invisible (see ro-fscheck.sh).
#
# Preconditions, checked before anything is copied: dest exists, has room for the data,
# and is not on the drive under test.
# Exit: 0 copied and verified, 1 read errors/mismatches/not enough room, 2 warnings
#       (some mounts skipped), 3 nothing to copy.

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

STEP=copy
ro_init "${1:-}"
DEST=${2:-}
shift $(( $# < 2 ? $# : 2 ))
CHECKSUM=0
for a in "$@"; do
  case $a in --checksum) CHECKSUM=1 ;; --loop) LOOP_WANTED=1 ;; *) echo "unknown option: $a"; exit 1 ;; esac
done
if [ -z "$DEST" ] || [ ! -d "$DEST" ]; then
  echo "Usage: $0 <serial> <dest-dir> [--checksum] [--loop]   (dest-dir must exist)"
  exit 1
fi
trap ro_cleanup EXIT

collect_parts
OUT=$DEST/$SERIAL
SUSPECT=$(attr_get "$SNAPDIR/baseline.attrs" suspect)
USE_IMAGE=0
[ "$SUSPECT" = yes ] && command -v ddrescue > /dev/null 2>&1 && USE_IMAGE=1

# Refuse a destination that lives on the drive being tested.
DESTSRC=$(findmnt -rn -T "$DEST" -o SOURCE 2>/dev/null | head -1)
for p in "$DEV" "${PARTS[@]}"; do
  if [ -n "$DESTSRC" ] && [ "$DESTSRC" = "$p" ]; then
    log "ABORT: destination $DEST is on $p, the drive under test"; exit 1
  fi
done

# Work out what will be read and how much room it needs.
JOBS=() NEED=0 WARNS=0
for p in "${PARTS[@]}"; do
  T=$(mount_target "$p")
  if [ -z "$T" ] && [ $USE_IMAGE = 0 ]; then
    log "WARN: $p is not mounted; nothing to copy from it"; WARNS=$((WARNS+1)); continue
  fi
  if [ -n "$T" ] && ! is_ro_mount "$p"; then
    log "WARN: $p is mounted read-write at $T; remount read-only first. Skipped"; WARNS=$((WARNS+1)); continue
  fi
  if [ $USE_IMAGE = 1 ]; then
    SZ=$($SUDO blockdev --getsize64 "$p")
  else
    SZ=$(df -B1 --output=used "$T" | tail -1 | tr -d ' ')
  fi
  NEED=$((NEED + SZ))
  JOBS+=("$p|$T")
done
if [ ${#JOBS[@]} -eq 0 ]; then
  log "SKIPPED: no read-only mounted filesystem to copy (mount them read-only first)"
  exit $RC_SKIP
fi

FREE=$(df -B1 --output=avail "$DEST" | tail -1 | tr -d ' ')
if [ "$FREE" -lt "$NEED" ]; then
  log "FAILED: need $NEED bytes at $DEST, only $FREE free"; exit $RC_FAIL
fi
mkdir -p "$OUT"
[ $USE_IMAGE = 1 ] && log "drive is suspect: imaging with ddrescue instead of copying files"

FAILS=0
for job in "${JOBS[@]}"; do
  P=${job%%|*}; T=${job#*|}
  NAME=$(basename "${T:-$P}")
  if [ $USE_IMAGE = 1 ]; then
    IMG=$OUT/$(basename "$P").img
    log "imaging $P -> $IMG"
    # -n: copy the easy areas first, skip scraping bad ones; re-run by hand to retry them.
    $SUDO ddrescue -n "$P" "$IMG" "$IMG.map" 2>&1 | tr '\r' '\n' | tail -5 >> "$LOG"
    RC=${PIPESTATUS[0]}
    if [ "$RC" -ne 0 ]; then log "FAILED: ddrescue exited $RC for $P (see $IMG.map)"; FAILS=$((FAILS+1)); else log "imaged $P"; fi
    continue
  fi

  log "copying $T -> $OUT/$NAME/"
  mkdir -p "$OUT/$NAME"
  # -rltD: contents, symlinks, times, devices. Ownership and permissions are meaningless
  # for most source filesystems (NTFS/APFS) and are not requested.
  $SUDO rsync -rltD --info=stats2 "$T"/ "$OUT/$NAME"/ >> "$LOG" 2>&1
  RC=$?
  case $RC in
    0) ;;
    24) log "WARN: some files vanished while copying $T"; WARNS=$((WARNS+1)) ;;
    *) log "FAILED: rsync exited $RC for $T (read errors above in $LOG)"; FAILS=$((FAILS+1)); continue ;;
  esac

  # Verify: a dry-run itemised compare. Quick mode trusts size+mtime; --checksum reads both
  # sides in full. Any itemised file line means source and copy differ.
  VOPT=(-rltD -n -i)
  [ $CHECKSUM = 1 ] && VOPT+=(--checksum)
  DIFF=$($SUDO rsync "${VOPT[@]}" "$T"/ "$OUT/$NAME"/ 2>&1 | grep -c -E '^(<f|>f|\*deleting|cL)')
  if [ "$DIFF" -eq 0 ]; then
    log "verified $T ($([ $CHECKSUM = 1 ] && echo checksum || echo size+mtime)): copy matches"
  else
    log "FAILED: $DIFF file(s) differ between $T and the copy"; FAILS=$((FAILS+1))
  fi
done

if [ $FAILS -gt 0 ]; then log "FAILED: $FAILS mount(s) with problems"; exit $RC_FAIL; fi
if [ $WARNS -gt 0 ]; then log "WARN: copied with $WARNS skipped or partial item(s)"; exit $RC_WARN; fi
log "PASSED: all mounted filesystems copied and verified"
exit $RC_PASS
