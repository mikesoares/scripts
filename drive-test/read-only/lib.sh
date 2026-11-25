#!/bin/bash
# Shared helpers for the read-only drive tests. Source it; do not execute it.
#
# Nothing in this suite ever opens a drive for writing: SMART reads, firmware
# self-tests (the drive reads itself), read-only badblocks, no-write filesystem
# checks, and reads from read-only mounts.
#
# Drives are identified by serial through /dev/disk/by-id, so /dev/sdX letters
# changing after a cable or controller swap cannot point a test at the wrong disk.
#
# Exit-code convention shared by every step script:
#   0 = PASSED   1 = FAILED   2 = WARN (passed with caveats)   3 = SKIPPED (could not run)
#
# Environment overrides (also used by the stub tests):
#   LOGDIR        where logs and snapshots go (default $HOME/drive-test-logs)
#   BYID_DIR      directory holding the by-id symlinks (default /dev/disk/by-id)
#   SUDO          privilege prefix (default "sudo"; set SUDO= when already root)
#   POLL_SECONDS  self-test poll interval (default 60)
#   RO_LOCK       1 (default): ro-baseline.sh locks the drive read-only in the kernel
#                 (blockdev --setro) at the start of a run; 0 skips the lock
#   SYS_BLOCK     sysfs block directory for the sectors-written counter (default /sys/block)

LOGDIR=${LOGDIR:-$HOME/drive-test-logs}
BYID_DIR=${BYID_DIR:-/dev/disk/by-id}
SUDO=${SUDO-sudo}
POLL_SECONDS=${POLL_SECONDS:-60}
RO_LOCK=${RO_LOCK:-1}

RC_PASS=0 RC_FAIL=1 RC_WARN=2 RC_SKIP=3

# log <message>: timestamped line to stdout and the drive's log.
log() { echo "[$(date '+%F %T')] ${STEP:+$STEP: }$*" | tee -a "$LOG"; }

# ro_init <serial>: resolve the drive and set SERIAL, ID, DEV, DEVNAME, LOG, SNAPDIR.
# Exits 1 if the serial matches no drive, or more than one.
ro_init() {
  SERIAL=${1:-}
  if [ -z "$SERIAL" ]; then
    echo "serial required" >&2
    exit 1
  fi
  mkdir -p "$LOGDIR"
  LOG=$LOGDIR/$SERIAL-ro.log
  SNAPDIR=$LOGDIR/$SERIAL-ro-snapshots
  mkdir -p "$SNAPDIR"
  local matches
  matches=$(ls "$BYID_DIR"/ata-*_"$SERIAL" "$BYID_DIR"/usb-*_"$SERIAL"* \
               "$BYID_DIR"/scsi-*"$SERIAL" "$BYID_DIR"/nvme-*_"$SERIAL" 2>/dev/null | grep -v -- '-part' || true)
  if [ -z "$matches" ]; then
    log "ABORT: no drive with serial $SERIAL under $BYID_DIR"
    exit 1
  fi
  # The same disk can have several by-id aliases; they must all resolve to one device.
  if [ "$(echo "$matches" | while read -r m; do readlink -f "$m"; done | sort -u | wc -l)" -ne 1 ]; then
    log "ABORT: serial $SERIAL matches more than one device"
    exit 1
  fi
  ID=$(echo "$matches" | head -1)
  DEV=$(readlink -f "$ID")
  DEVNAME=$(basename "$DEV")
}

# detect_smart: sets SMART_OK (0/1) and SMART_OPTS; tries native, then SAT passthrough
# (USB bridges). Returns 1 when the drive offers no SMART at all.
detect_smart() {
  SMART_OPTS=()
  SMART_OK=0
  local o
  for o in "" "-d sat"; do
    # $o is deliberately unquoted: it is zero or two words.
    # shellcheck disable=SC2086
    if $SUDO smartctl $o -H "$DEV" 2>/dev/null | grep -q -E 'SMART overall-health|SMART Health Status'; then
      read -r -a SMART_OPTS <<<"$o"
      SMART_OK=1
      return 0
    fi
  done
  return 1
}

# smart <smartctl args>: run smartctl on the drive with the detected options.
smart() { $SUDO smartctl "${SMART_OPTS[@]}" "$@" "$DEV"; }

is_nvme() { [[ $DEV == /dev/nvme* ]]; }

# health: PASSED | FAILED | UNKNOWN
health() {
  local h
  h=$(smart -H 2>/dev/null | grep -E 'overall-health|Health Status' | head -1)
  case $h in
    *PASSED* | *OK*) echo PASSED ;;
    *FAILED*) echo FAILED ;;
    *) echo UNKNOWN ;;
  esac
}

# attr_raw <id>: raw value of an ATA SMART attribute (first token), empty if absent.
attr_raw() { smart -A 2>/dev/null | awk -v id="$1" '$1==id { print $10; exit }'; }

# attr_norm <id>: normalised value (column 4) of an ATA SMART attribute.
attr_norm() { smart -A 2>/dev/null | awk -v id="$1" '$1==id { print $4+0; exit }'; }

# nvme_field <label>: value of a "Label: value" line from the NVMe health log.
nvme_field() { smart -A 2>/dev/null | awk -F: -v l="$1" '$1==l { gsub(/[ ,]/, "", $2); print $2+0; exit }'; }

# errlog_count: number of entries in the ATA error log, "?" if it cannot be read.
errlog_count() {
  local out n
  out=$(smart -l error 2>/dev/null)
  n=$(echo "$out" | sed -n 's/^ATA Error Count: *\([0-9]*\).*/\1/p' | head -1)
  if [ -n "$n" ]; then
    echo "$n"
  elif echo "$out" | grep -q 'No Errors Logged'; then
    echo 0
  else
    echo '?'
  fi
}

# attr_get <file> <key>: value of key=value in a snapshot .attrs file.
attr_get() { sed -n "s/^$2=//p" "$1" 2>/dev/null | head -1; }

# sectors_written: sectors written to the whole drive since boot (field 7 of the sysfs stat
# file; empty if unreadable). Zero-length flushes do not count, so a read-only run keeps it flat.
sectors_written() { awk '{print $7}' "${SYS_BLOCK:-/sys/block}/$DEVNAME/stat" 2>/dev/null; }

# ro_flag: the kernel's read-only flag of the drive (1 = writes are refused).
ro_flag() { $SUDO blockdev --getro "$DEV" 2>/dev/null; }

# lock_device: make the kernel refuse every write to the drive and its partitions, as a second
# layer under the read-only commands. Returns 0 locked, 1 failed, 2 skipped (RO_LOCK=0). The
# flag lasts until reboot; undo with `blockdev --setrw`. SMART commands and self-tests still work.
lock_device() {
  if [ "$RO_LOCK" != 1 ]; then
    log "read-only lock skipped (RO_LOCK=$RO_LOCK)"; return 2
  fi
  local p
  $SUDO blockdev --setro "$DEV" 2>/dev/null
  while read -r p; do
    [ -n "$p" ] && $SUDO blockdev --setro "$p" 2>/dev/null
  done < <(lsblk -lnpo NAME,TYPE "$DEV" 2>/dev/null | awk '$2=="part" { print $1 }')
  if [ "$(ro_flag)" = 1 ]; then
    log "locked $DEV read-only (blockdev --setro, until reboot); undo with: sudo blockdev --setrw $DEV"
    return 0
  fi
  log "WARN: could not lock $DEV read-only; the commands in this suite still never write"
  return 1
}

# record_writes <label>: save the sectors-written counter and read-only flag next to the
# SMART snapshot of the same label (ro-compare.sh compares "baseline" with the later state).
record_writes() {
  { echo "sectors_written=$(sectors_written)"; echo "ro_flag=$(ro_flag)"; } > "$SNAPDIR/$1.writes"
}

# collect_parts: set PARTS to every block device carrying a filesystem for this drive:
# its partitions (or the bare disk if it holds a filesystem directly) plus the partitions
# of any loop device already backed by it, and of the one --loop attaches. Set LOOP_WANTED=1
# first to attach a read-only 4096-byte-sector loop device when none exists (for drives
# formatted with 4096-byte logical sectors but presenting 512, where the kernel sees no
# partitions); it is detached again by ro_cleanup.
collect_parts() {
  PARTS=()
  local base=("$DEV") d p
  while read -r d; do
    [ -n "$d" ] && base+=("$d")
  done < <($SUDO losetup -j "$DEV" 2>/dev/null | cut -d: -f1)
  if [ "${LOOP_WANTED:-0}" = 1 ] && [ ${#base[@]} -eq 1 ] && [ -z "$(lsblk -lnpo NAME,TYPE "$DEV" | awk '$2=="part"')" ]; then
    LOOPDEV=$($SUDO losetup -r -P --sector-size 4096 -f --show "$DEV") && base+=("$LOOPDEV")
  fi
  for d in "${base[@]}"; do
    local found=0
    while read -r p; do
      [ -n "$p" ] && PARTS+=("$p") && found=1
    done < <(lsblk -lnpo NAME,TYPE "$d" | awk '$2=="part" { print $1 }')
    # No partitions: the disk itself may carry a filesystem.
    if [ "$found" = 0 ] && [ -n "$($SUDO blkid -o value -s TYPE "$d" 2>/dev/null)" ]; then
      PARTS+=("$d")
    fi
  done
}

# ro_cleanup: detach a loop device this run created. Install with: trap ro_cleanup EXIT
ro_cleanup() {
  if [ -n "${LOOPDEV:-}" ]; then
    $SUDO losetup -d "$LOOPDEV" 2>/dev/null
  fi
}

# mount_opts <device>: mount options of a mounted block device ("" if unmounted).
mount_opts() { findmnt -rn -S "$1" -o OPTIONS 2>/dev/null | head -1; }
# mount_target <device>: mount point of a block device ("" if unmounted).
mount_target() { findmnt -rn -S "$1" -o TARGET 2>/dev/null | head -1; }
# is_ro_mount <device>: true when mounted and "ro" is among its options.
is_ro_mount() { mount_opts "$1" | tr ',' '\n' | grep -qx ro; }
