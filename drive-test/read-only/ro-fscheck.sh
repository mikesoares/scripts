#!/bin/bash
# Check each filesystem on the drive with that filesystem's no-write checker. Never repairs.
# Usage: ./ro-fscheck.sh <serial> [--loop]
# Example: ./ro-fscheck.sh ABC123XYZ
#
# --loop   for a drive formatted with 4096-byte logical sectors that presents 512-byte
#          sectors (the kernel then sees no partitions): attach a read-only loop device
#          with the right sector size for the duration of the run, and check its partitions.
#
# Per filesystem (always in report-only mode, never -a/-p/-y/repair):
#   ntfs      ntfsfix --no-action          ext2/3/4  e2fsck -fn
#   vfat      fsck.vfat -n                 xfs       xfs_repair -n
#   btrfs     btrfs check --readonly
# Anything else (APFS, exFAT without a checker, LUKS, ...) is reported as "no checker"
# and counts as a warning, not a failure: rely on SMART and the file copy for those.
# A filesystem that is mounted read-write is skipped (a check of a live rw filesystem is
# unreliable); read-only mounts are checked, but xfs and btrfs checkers refuse mounted
# filesystems and are reported as skipped.
#
# Exit: 0 all checkable filesystems clean, 1 damage reported, 2 warnings only, 3 nothing found.

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

STEP=fscheck
ro_init "${1:-}"
[ "${2:-}" = --loop ] && LOOP_WANTED=1
trap ro_cleanup EXIT

collect_parts
if [ ${#PARTS[@]} -eq 0 ]; then
  log "SKIPPED: no filesystems found on $DEV ($SERIAL); if the drive uses 4096-byte sectors try --loop"
  exit $RC_SKIP
fi

FAILS=0 WARNS=0 CHECKED=0
for p in "${PARTS[@]}"; do
  FS=$($SUDO blkid -o value -s TYPE "$p" 2>/dev/null)
  OPTS=$(mount_opts "$p")
  if [ -n "$OPTS" ] && ! is_ro_mount "$p"; then
    log "WARN: $p ($FS) is mounted read-write; skipped"; WARNS=$((WARNS+1)); continue
  fi
  MOUNTED=0; [ -n "$OPTS" ] && MOUNTED=1

  case $FS in
    ntfs)
      CMD=(ntfsfix --no-action "$p") ;;
    ext2 | ext3 | ext4)
      CMD=(e2fsck -fn "$p") ;;
    vfat)
      CMD=(fsck.vfat -n "$p") ;;
    xfs)
      if [ $MOUNTED = 1 ]; then log "WARN: $p (xfs) is mounted; xfs_repair -n needs it unmounted; skipped"; WARNS=$((WARNS+1)); continue; fi
      CMD=(xfs_repair -n "$p") ;;
    btrfs)
      if [ $MOUNTED = 1 ]; then log "WARN: $p (btrfs) is mounted; btrfs check needs it unmounted; skipped"; WARNS=$((WARNS+1)); continue; fi
      CMD=(btrfs check --readonly "$p") ;;
    *)
      log "WARN: $p has filesystem '${FS:-unknown}': no read-only checker; skipped"; WARNS=$((WARNS+1)); continue ;;
  esac

  if ! command -v "${CMD[0]}" > /dev/null 2>&1; then
    log "WARN: ${CMD[0]} is not installed; $p ($FS) not checked"; WARNS=$((WARNS+1)); continue
  fi

  CHECKED=$((CHECKED+1))
  OUT=$($SUDO "${CMD[@]}" 2>&1); RC=$?
  echo "$OUT" >> "$LOG"
  if [ $RC -ne 0 ]; then
    log "FAILED: ${CMD[*]} exited $RC"; FAILS=$((FAILS+1))
  elif echo "$OUT" | grep -q -i -E 'dirty|hibernat|inconsisten|not cleanly|scheduled for check'; then
    # e.g. an NTFS volume left dirty or hibernated by Windows: readable, but not clean.
    log "WARN: $p ($FS) is flagged dirty or inconsistent but the check exited 0"; WARNS=$((WARNS+1))
  else
    log "clean: $p ($FS)"
  fi
done

if [ $FAILS -gt 0 ]; then log "FAILED: $FAILS filesystem(s) reported damage"; exit $RC_FAIL; fi
if [ $CHECKED -eq 0 ]; then log "WARN: no filesystem could be checked"; exit $RC_WARN; fi
if [ $WARNS -gt 0 ]; then log "WARN: $CHECKED checked clean, $WARNS skipped or flagged"; exit $RC_WARN; fi
log "PASSED: $CHECKED filesystem(s) clean"
exit $RC_PASS
