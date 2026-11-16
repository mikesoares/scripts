#!/bin/bash
# Run ZFS pool test + f3 + scrub + final SMART for one drive, then judge the results.
# Usage: ./drive-test-phase2.sh <serial|drive_letter>
#   serial (preferred): e.g. WD-WCAZA6147923; the drive is found through
#     /dev/disk/by-id/ata-*_<serial>, so /dev/sdX letters moving after a controller
#     or cable swap cannot point the test at the wrong disk.
#   drive_letter: a single letter, e.g. a for /dev/sda (legacy).
#
# Ends with exactly one verdict line in the log:
#   "PHASE 2 COMPLETE" - every check was clean
#   "PHASE 2 FAILED"   - one or more checks failed (reasons are listed above it)
# A step that errors out does not abort the run; it is recorded as a failure so
# the verdict is always written (check-complete.sh depends on that).

L=${1:-}
if [ -z "$L" ]; then
  echo "Usage: $0 <serial|drive_letter>"
  exit 1
fi
if [[ "$L" =~ ^[a-z]$ ]]; then
  DEV=/dev/sd$L
  POOL=TEST${L^^}
  LOG=/tmp/sd$L-phase2.log
else
  ID=$(ls /dev/disk/by-id/ata-*_"$L" 2>/dev/null | grep -v -- '-part' | head -1)
  if [ -z "$ID" ]; then
    echo "ABORT: no drive with serial $L under /dev/disk/by-id"
    exit 1
  fi
  DEV=$(readlink -f "$ID")
  # ZFS pool names allow letters, digits, '_', '-', '.', ':' but must start with a letter.
  POOL=TEST_$(echo "$L" | tr -c 'A-Za-z0-9\n' '_')
  LOG=/tmp/$L-phase2.log
fi
# Refuse a drive with a mounted filesystem (e.g. the boot disk).
if [ -n "$(lsblk -no MOUNTPOINTS "$DEV" | tr -d '[:space:]')" ]; then
  echo "ABORT: $DEV has a mounted filesystem"
  exit 1
fi
MNT=/$POOL
FAILS=()

exec > >(tee -a "$LOG") 2>&1

fail() { FAILS+=("$1"); echo "!! FAIL: $1"; }

# Prints SMART attribute raw values that must be 0 on a healthy drive:
# 5 Reallocated_Sector_Ct, 197 Current_Pending_Sector, 198 Offline_Uncorrectable.
# Raw value is column 10 of `smartctl -A`.
smart_bad_attrs() {
  sudo smartctl -A "$DEV" | awk '$1==5||$1==197||$1==198 { if ($10+0 != 0) print $1" "$2"="$10 }'
}

echo "===== PHASE 2 START: $DEV (pool $POOL) at $(date) ====="

# Identify the drive (wwn/serial) so the result can be tied to a physical disk
# even if /dev/sdX letters move between boots.
echo "[$(date)] Drive identity"
sudo smartctl -i "$DEV" | grep -E 'Device Model|Model Family|Serial Number|LU WWN|User Capacity'

echo "[$(date)] Pre-test SMART attributes"
sudo smartctl -A "$DEV"
BAD=$(smart_bad_attrs)
[ -n "$BAD" ] && fail "SMART before test: $(echo "$BAD" | tr '\n' ';')"

echo "[$(date)] Step 3: Creating ZFS pool $POOL on $DEV"
# Compression must stay on: the original methodology found checksum errors
# that only appeared with compress=lz4.
if sudo zpool create -f -o ashift=12 -O logbias=throughput -O compress=lz4 -O dedup=off -O atime=off -O xattr=sa "$POOL" "${ID:-$DEV}" \
  && sudo zpool export "$POOL" \
  && sudo zpool import -d /dev/disk/by-id "$POOL"; then
  sudo chmod -R ugo+rw "$MNT"
  sudo zpool status "$POOL"

  echo "[$(date)] Step 4a: f3write $MNT"
  sudo f3write "$MNT" || fail "f3write exited non-zero"

  echo "[$(date)] Step 4b: f3read $MNT"
  F3OUT=$(sudo f3read "$MNT" 2>&1) || fail "f3read exited non-zero"
  echo "$F3OUT"
  # Any non-zero sector count on LOST/Corrupted/Slightly changed/Overwritten is a failure.
  F3BAD=$(echo "$F3OUT" | grep -E '^[[:space:]]*(Data LOST|Corrupted|Slightly changed|Overwritten)' | grep -v '(0 sectors)')
  [ -n "$F3BAD" ] && fail "f3read reported damage: $(echo "$F3BAD" | tr -s ' ' | tr '\n' ';')"

  echo "[$(date)] Step 5a: zpool scrub $POOL"
  sudo zpool scrub "$POOL"
  # `zpool scrub` returns immediately; wait for it to finish
  while sudo zpool status "$POOL" | grep -q 'scrub in progress'; do
    echo "  ...scrub still running at $(date)"
    sleep 60
  done
  sudo zpool status "$POOL"
  # Rows between the NAME header and the "errors:" line: fields 3-5 are READ WRITE CKSUM.
  ZERR=$(sudo zpool status "$POOL" | awk '/^errors:/{f=0} f && NF>=5 && ($3!="0"||$4!="0"||$5!="0") {print $1" R="$3" W="$4" CK="$5} /NAME +STATE/{f=1}')
  [ -n "$ZERR" ] && fail "ZFS errors: $(echo "$ZERR" | tr '\n' ';') (checksum-only errors can be a bad cable/HBA; re-seat and retest)"
  sudo zpool status "$POOL" | grep -q 'errors: No known data errors' || fail "zpool reports data errors"
else
  fail "ZFS pool create/export/import failed; skipped f3 and scrub"
fi

echo "[$(date)] Step 5b: smartctl long test on $DEV"
sudo smartctl -t long "$DEV" || fail "could not start SMART long test"
# poll SMART status
while sudo smartctl -c "$DEV" | grep -q 'Self-test routine in progress'; do
  echo "  ...SMART long test still running at $(date)"
  sleep 300
done
SELFTEST=$(sudo smartctl -l selftest "$DEV" | head -8)
echo "$SELFTEST"
# Newest self-test is the first numbered entry (#1)
echo "$SELFTEST" | grep -E '^# ?1 ' | grep -q 'Completed without error' || fail "SMART long test did not complete without error"
sudo smartctl -A "$DEV"
BAD=$(smart_bad_attrs)
[ -n "$BAD" ] && fail "SMART after test: $(echo "$BAD" | tr '\n' ';')"

echo
if [ ${#FAILS[@]} -eq 0 ]; then
  echo "===== PHASE 2 COMPLETE: $DEV at $(date) ====="
else
  echo "Failures (${#FAILS[@]}):"
  printf '  - %s\n' "${FAILS[@]}"
  echo "===== PHASE 2 FAILED: $DEV at $(date) ====="
fi
