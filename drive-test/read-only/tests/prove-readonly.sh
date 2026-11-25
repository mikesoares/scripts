#!/bin/bash
# Opt-in proof that the read-only suite writes nothing, using real tools on a throwaway
# sparse disk image attached as a loop device. No real drive is touched. Needs root, and
# mkntfs (ntfs-3g), sfdisk, mkfs.ext4, e2fsck, badblocks, rsync, blockdev, losetup.
# Usage: sudo ./tests/prove-readonly.sh [path-to-read-only-dir]   (default: this script's parent)
# Env:   WORK  scratch directory (default /var/tmp/ro-proof; removed at the start of a run)
#
# What it does:
#   control  a deliberate 4 KiB write to a copy of the image must move the write counters
#            and change the SHA-256, proving both detectors can see a write
#   test 3   SHA-256 of the image before/after the full suite, device attached READ-WRITE
#   test 1   sectors written (/sys/block/loopN/stat) before/after the suite
#   test 2   `blockdev --setro` on the device and partitions, then the suite again: it must
#            still pass its filesystem, copy and surface steps, a deliberate write must be
#            refused, and the kernel log must show no write attempts
#   test 4   the suite locks the drive itself: ro-baseline.sh on a read-write device must
#            set the kernel read-only flag, a deliberate write must then be refused, and
#            ro-compare.sh must report no sectors written (tests 3 and 2 run with RO_LOCK=0,
#            so that test 3 really is read-write and test 2 uses its own lock)
# The overall verdict is WARN, not PASSED, because a loop device has no SMART; the SMART
# steps are skipped. Exit status: 0 all checks passed, 1 any failed, 2 not run (not root).
set -u
[ "$(id -u)" -eq 0 ] || { echo "must run as root"; exit 2; }
RO=$(readlink -f "${1:-$(dirname "$(readlink -f "$0")")/..}"); W=${WORK:-/var/tmp/ro-proof}
# The scratch dir is deleted, so refuse anything that is not clearly a dedicated path.
case $W in /var/tmp/* | /tmp/*) ;; *) [ -n "${WORK_OK:-}" ] || { echo "WORK must be under /var/tmp or /tmp (or set WORK_OK=1)"; exit 2; } ;; esac
rm -rf "$W"; mkdir -p "$W"/{mnt/ntfs,mnt/ext,dest,byid,logs}; cd "$W" || exit 1
PASS=0 FAIL=0
ok()  { echo "PASS  $*"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $*"; FAIL=$((FAIL+1)); }
LOOP=""
cleanup() { umount "$W"/mnt/* 2>/dev/null; [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null; }
trap cleanup EXIT
stat_w() { awk '{print $7}' /sys/block/"$(basename "$1")"/stat; }   # sectors written (data)
stat_full() { cat /sys/block/"$(basename "$1")"/stat; }

echo "== build a 2 GiB sparse image: NTFS + ext4 partitions with data =="
truncate -s 2G disk.img
LOOP=$(losetup -P -f --show disk.img)
printf 'label: dos\n,1G,7\n,,83\n' | sfdisk -q "$LOOP"; partprobe "$LOOP" 2>/dev/null; sleep 1
mkntfs -F -Q -q -L PROOFNTFS "${LOOP}p1" >/dev/null
mkfs.ext4 -q -F -L proofext "${LOOP}p2"
mount -t ntfs3 "${LOOP}p1" mnt/ntfs; mount "${LOOP}p2" mnt/ext
for m in ntfs ext; do
  mkdir -p mnt/$m/d1/d2; for i in 1 2 3 4 5; do head -c $((RANDOM*200+5000)) /dev/urandom > mnt/$m/d1/f$i; head -c 40000 /dev/urandom > mnt/$m/d1/d2/g$i; done
done
sync; umount mnt/ntfs mnt/ext; losetup -d "$LOOP"; LOOP=""
echo "== (control) the detectors catch a single deliberate write =="
cp --sparse=always disk.img control.img; H=$(sha256sum control.img | cut -d' ' -f1)
CL=$(losetup -f --show control.img); B=$(stat_w "$CL")
head -c 4096 /dev/zero | tr '\0' X | dd of="$CL" bs=4096 seek=366211 oflag=direct conv=notrunc 2>/dev/null; sync
A=$(stat_w "$CL"); losetup -d "$CL"
[ "$A" != "$B" ] && ok "counter control: write counters moved ($B -> $A)" || bad "counter control did not move"
[ "$(sha256sum control.img | cut -d' ' -f1)" != "$H" ] && ok "hash control: 1 changed byte changes the SHA-256" || bad "hash control did not change"
rm -f control.img

H0=$(sha256sum disk.img | cut -d' ' -f1); echo "image sha256 before: $H0"
LOOP=$(losetup -P -f --show disk.img)     # attached READ-WRITE on purpose: a write would succeed
ln -s "$LOOP" byid/ata-PROOF_PROOFSERIAL
mount -t ntfs3 -o ro "${LOOP}p1" mnt/ntfs; mount -o ro "${LOOP}p2" mnt/ext
export BYID_DIR=$W/byid LOGDIR=$W/logs SUDO= POLL_SECONDS=0 START_GRACE_SECONDS=0 RO_LOCK=0

run() { # <name> <args...>
  local n=$1; shift
  "$RO/ro-run.sh" PROOFSERIAL "$@" > "$W/run-$n.out" 2>&1; echo "ro-run $n exit=$? ; verdict: $(grep -o 'RO-CHECK [A-Z]* PROOFSERIAL' "$W/run-$n.out" | tail -1)"
}

echo; echo "== TEST 3: full-image hash, device attached read-write =="
S0=$(stat_w "$LOOP"); F0=$(stat_full "$LOOP")
run copy --dest "$W/dest" --checksum
run surface
S1=$(stat_w "$LOOP"); F1=$(stat_full "$LOOP"); echo "stat before: $F0"; echo "stat after:  $F1"
sync; umount mnt/ntfs mnt/ext
losetup -d "$LOOP"; H1=$(sha256sum disk.img | cut -d' ' -f1); echo "image sha256 after:  $H1"
[ "$H0" = "$H1" ] && ok "TEST 3: image is byte-for-byte identical after the full suite" || bad "TEST 3: image CHANGED"
[ "$S0" = "$S1" ] && ok "TEST 1: sectors written unchanged across the suite ($S0)" || bad "TEST 1: sectors written moved $S0 -> $S1"
[ "$(find dest -type f | wc -l)" -ge 20 ] && ok "copy step really read the data ($(find dest -type f | wc -l) files copied)" || bad "copy produced too few files"

echo; echo "== TEST 2: kernel read-only lock on the device, suite must still pass =="
LOOP=$(losetup -P -f --show disk.img); rm -f byid/*; ln -s "$LOOP" byid/ata-PROOF_PROOFSERIAL
for d in "$LOOP" "${LOOP}"p1 "${LOOP}"p2; do blockdev --setro "$d"; done
[ "$(blockdev --getro "$LOOP")" = 1 ] && ok "device is locked read-only by the kernel (blockdev --getro = 1)" || bad "setro did not stick"
if dd if=/dev/zero of="$LOOP" bs=512 count=1 oflag=direct 2>/dev/null; then bad "control: write to the locked device SUCCEEDED"; else ok "control: a deliberate write to the locked device is refused"; fi
mount -t ntfs3 -o ro "${LOOP}p1" mnt/ntfs; mount -o ro "${LOOP}p2" mnt/ext
dmesg -C 2>/dev/null; S2=$(stat_w "$LOOP"); rm -rf dest/* logs/*
run locked --dest "$W/dest" --checksum; run lockedsurf
S3=$(stat_w "$LOOP")
if dmesg | grep -i -E 'read-only|Operation not permitted|I/O error' | grep -i "$(basename "$LOOP")" ; then bad "kernel logged write attempts/errors on the locked device"; else ok "no write attempts or I/O errors logged by the kernel"; fi
[ "$S2" = "$S3" ] && ok "TEST 1 (again, locked): sectors written unchanged" || bad "sectors written moved while locked $S2 -> $S3"
if ! grep -q 'RO-CHECK FAILED' "$W/run-locked.out" "$W/run-lockedsurf.out" && grep -q 'fscheck: PASSED' "$W/run-locked.out" && grep -q 'copy: PASSED' "$W/run-locked.out" && grep -q 'surface: PASSED' "$W/run-lockedsurf.out"; then ok "fscheck, copy+verify and surface scan all PASSED on the write-locked device (WARN overall only because a loop device has no SMART)"; else bad "a step failed when the device was locked"; tail -15 "$W/run-locked.out"; fi
umount mnt/ntfs mnt/ext; blockdev --setrw "$LOOP" 2>/dev/null; losetup -d "$LOOP"; LOOP=""
H2=$(sha256sum disk.img | cut -d' ' -f1); [ "$H0" = "$H2" ] && ok "image still identical after the locked run" || bad "image changed after locked run"
echo; echo "== TEST 4: the suite locks the drive itself (RO_LOCK default) =="
LOOP=$(losetup -P -f --show disk.img); rm -f byid/*; ln -s "$LOOP" byid/ata-PROOF_PROOFSERIAL; rm -rf logs/*
[ "$(blockdev --getro "$LOOP")" = 0 ] && ok "device starts read-write" || bad "device did not start read-write"
RO_LOCK=1 "$RO/ro-baseline.sh" PROOFSERIAL > "$W/run-lock.out" 2>&1
[ "$(blockdev --getro "$LOOP")" = 1 ] && ok "TEST 4: ro-baseline.sh locked the device (blockdev --getro = 1)" || bad "TEST 4: ro-baseline.sh did not lock the device"
if dd if=/dev/zero of="$LOOP" bs=512 count=1 oflag=direct 2>/dev/null; then bad "TEST 4: write to the suite-locked device SUCCEEDED"; else ok "TEST 4: a deliberate write to the suite-locked device is refused"; fi
RO_LOCK=1 "$RO/ro-compare.sh" PROOFSERIAL > "$W/run-lockcmp.out" 2>&1
grep -q "no sectors written" "$W/run-lockcmp.out" && ok "TEST 4: ro-compare.sh reports no sectors written" || { bad "TEST 4: ro-compare.sh did not report an unchanged write counter"; tail -5 "$W/run-lockcmp.out"; }
blockdev --setrw "$LOOP" 2>/dev/null; losetup -d "$LOOP"; LOOP=""
H3=$(sha256sum disk.img | cut -d' ' -f1); [ "$H0" = "$H3" ] && ok "image still identical after test 4" || bad "image changed after test 4"
echo; echo "== $PASS passed, $FAIL failed =="; echo "full run output in $W/run-*.out"
[ "$FAIL" -eq 0 ]
