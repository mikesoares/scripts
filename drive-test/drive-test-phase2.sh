#!/bin/bash
# Run ZFS pool test + f3 + scrub + final SMART for one drive
# Usage: ./drive-test-phase2.sh <drive_letter>  (e.g., a for /dev/sda)

set -e
L=$1
DEV=/dev/sd$L
POOL=TEST${L^^}
MNT=/$POOL
LOG=/tmp/sd$L-phase2.log

exec > >(tee -a $LOG) 2>&1

echo "===== PHASE 2 START: $DEV (pool $POOL) at $(date) ====="

echo "[$(date)] Step 3: Creating ZFS pool $POOL on $DEV"
sudo zpool create -f -o ashift=12 -O logbias=throughput -O compress=lz4 -O dedup=off -O atime=off -O xattr=sa $POOL $DEV
sudo zpool export $POOL
sudo zpool import -d /dev/disk/by-id $POOL
sudo chmod -R ugo+rw $MNT
sudo zpool status $POOL

echo "[$(date)] Step 4a: f3write $MNT"
sudo f3write $MNT

echo "[$(date)] Step 4b: f3read $MNT"
sudo f3read $MNT

echo "[$(date)] Step 5a: zpool scrub $POOL"
sudo zpool scrub $POOL
# wait for scrub to finish
while sudo zpool status $POOL | grep -q 'scrub in progress'; do
  echo "  ...scrub still running at $(date)"
  sleep 60
done
sudo zpool status $POOL

echo "[$(date)] Step 5b: smartctl long test on $DEV"
sudo smartctl -t long $DEV
# poll SMART status
while sudo smartctl -c $DEV | grep -q 'Self-test routine in progress'; do
  echo "  ...SMART long test still running at $(date)"
  sleep 300
done
sudo smartctl -l selftest $DEV | head -8
sudo smartctl -A $DEV

echo "===== PHASE 2 COMPLETE: $DEV at $(date) ====="
