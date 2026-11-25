#!/bin/bash
# Stub-driven tests for the read-only suite. Runs every script against fake commands
# (tests/stubs/*) and a throwaway directory; no real drive and no root are involved.
# Usage: ./tests/run-tests.sh
# Real rsync is used for the copy test, between two temp directories.

HERE=$(dirname "$(readlink -f "$0")")
RO=$(dirname "$HERE")
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

export STUB=$T/stub BYID_DIR=$T/byid LOGDIR=$T/logs SUDO= POLL_SECONDS=0 START_GRACE_SECONDS=0
export PATH=$HERE/stubs:$PATH
mkdir -p "$STUB" "$BYID_DIR" "$LOGDIR" "$T/dev"
touch "$T/dev/sdz"
ln -s "$T/dev/sdz" "$BYID_DIR/ata-STUBDISK_TESTSERIAL1"
S=TESTSERIAL1
LOG=$LOGDIR/$S-ro.log

PASS=0 FAIL=0
# expect <name> <expected-rc> <command...>
expect() {
  local name=$1 want=$2; shift 2
  "$@" > "$T/out" 2>&1; local rc=$?
  if [ "$rc" -eq "$want" ]; then PASS=$((PASS+1)); echo "ok   $name"
  else FAIL=$((FAIL+1)); echo "FAIL $name (rc $rc, wanted $want)"; sed 's/^/     | /' "$T/out" | tail -8; fi
}
# reset: clear scenario state between tests.
reset() { rm -rf "$STUB" "$LOGDIR"; mkdir -p "$STUB" "$LOGDIR"; }
setv() { echo "$2" > "$STUB/$1"; }

# --- baseline ---
reset; expect "baseline clean drive" 0 "$RO/ro-baseline.sh" $S
grep -q '^suspect=no' "$LOGDIR/$S-ro-snapshots/baseline.attrs" && echo "ok   baseline records suspect=no" && PASS=$((PASS+1)) || { echo "FAIL suspect=no"; FAIL=$((FAIL+1)); }
reset; setv A197 3; expect "baseline with pending sectors is WARN" 2 "$RO/ro-baseline.sh" $S
grep -q '^suspect=yes' "$LOGDIR/$S-ro-snapshots/baseline.attrs" && PASS=$((PASS+1)) && echo "ok   baseline records suspect=yes" || { echo "FAIL suspect=yes"; FAIL=$((FAIL+1)); }
reset; setv errcount 4; expect "baseline with ATA error log is WARN" 2 "$RO/ro-baseline.sh" $S
reset; touch "$STUB/nosmart"; expect "baseline with no SMART is SKIPPED" 3 "$RO/ro-baseline.sh" $S
reset; touch "$STUB/needsat"; expect "baseline falls back to -d sat" 0 "$RO/ro-baseline.sh" $S
reset; expect "unknown serial aborts" 1 "$RO/ro-baseline.sh" NOSUCHSERIAL

# --- selftest ---
reset; expect "short self-test passes" 0 "$RO/ro-selftest.sh" $S short
reset; setv selftest "# 1  Extended offline    Completed: read failure       90%       100         12345"
expect "self-test read failure fails" 1 "$RO/ro-selftest.sh" $S long
reset; touch "$STUB/nosmart"; expect "self-test without SMART skipped" 3 "$RO/ro-selftest.sh" $S short
expect "bad self-test kind rejected" 1 "$RO/ro-selftest.sh" $S bogus

# --- compare ---
reset; "$RO/ro-baseline.sh" $S > /dev/null
expect "compare unchanged drive passes" 0 "$RO/ro-compare.sh" $S
setv A197 5; expect "compare: pending sectors rose fails" 1 "$RO/ro-compare.sh" $S
setv A197 0; setv A199 9; expect "compare: CRC rose is WARN" 2 "$RO/ro-compare.sh" $S
setv A199 0; setv errcount 2; expect "compare: error log grew fails" 1 "$RO/ro-compare.sh" $S
setv errcount 0; echo "kernel: blk_update_request: I/O error, dev sdz, sector 123" > "$STUB/klog"
expect "compare: kernel I/O error on the device fails" 1 "$RO/ro-compare.sh" $S
echo "kernel: ata3: hard resetting link" > "$STUB/klog"; expect "compare: link reset is WARN" 2 "$RO/ro-compare.sh" $S
reset; expect "compare without baseline skipped" 3 "$RO/ro-compare.sh" $S

# --- surface scan ---
reset; expect "surface scan clean" 0 "$RO/ro-surface-scan.sh" $S
reset; printf '1234\n' > "$STUB/bad"; expect "surface scan with bad block fails" 1 "$RO/ro-surface-scan.sh" $S
if grep -q -E '(^| )-(w|n)( |$)' "$STUB/badblocks-args"; then FAIL=$((FAIL+1)); echo "FAIL badblocks got a write flag"; else PASS=$((PASS+1)); echo "ok   badblocks never given -w or -n"; fi

# --- fscheck ---
fsenv() { # <fs> [mount-line]
  setv parts_sdz 1; echo "$1" > "$STUB/fs_sdz1"; [ -n "$2" ] && echo "$2" > "$STUB/mnt_sdz1"; }
reset; fsenv ntfs "/mnt/x ro,nosuid"; expect "fscheck clean NTFS" 0 "$RO/ro-fscheck.sh" $S
reset; fsenv ntfs "/mnt/x ro"; setv ntfsfix_rc 1; expect "fscheck NTFS damage fails" 1 "$RO/ro-fscheck.sh" $S
reset; fsenv ntfs "/mnt/x ro"; echo "Volume is dirty" > "$STUB/ntfsfix_out"; expect "fscheck dirty NTFS is WARN" 2 "$RO/ro-fscheck.sh" $S
reset; fsenv apfs "/mnt/x ro"; expect "fscheck APFS has no checker (WARN)" 2 "$RO/ro-fscheck.sh" $S
reset; fsenv ntfs "/mnt/x rw"; expect "fscheck skips rw mount (WARN)" 2 "$RO/ro-fscheck.sh" $S
reset; expect "fscheck finds nothing (skipped)" 3 "$RO/ro-fscheck.sh" $S

# --- copy-verify ---
SRC=$T/src; DST=$T/dst; mkdir -p "$SRC/sub" "$DST"; echo hello > "$SRC/a.txt"; echo world > "$SRC/sub/b.txt"
reset; fsenv ntfs "$SRC ro"; expect "copy-verify copies and verifies" 0 "$RO/ro-copy-verify.sh" $S "$DST" --checksum
[ "$(cat "$DST/$S/src/sub/b.txt" 2>/dev/null)" = world ] && PASS=$((PASS+1)) && echo "ok   copied file content matches" || { FAIL=$((FAIL+1)); echo "FAIL copied file missing"; }
reset; fsenv ntfs "$SRC rw"; expect "copy-verify refuses rw mount" 3 "$RO/ro-copy-verify.sh" $S "$DST"
reset; fsenv ntfs "$SRC ro"; echo "$T/dev/sdz1" > "$STUB/destsrc"; expect "copy-verify refuses dest on the drive" 1 "$RO/ro-copy-verify.sh" $S "$DST"
reset; expect "copy-verify with no mounts skipped" 3 "$RO/ro-copy-verify.sh" $S "$DST"
expect "copy-verify needs an existing dest" 1 "$RO/ro-copy-verify.sh" $S "$T/nonexistent"

# --- orchestrator ---
reset; fsenv ntfs "$SRC ro"; expect "ro-run clean (no dest: surface scan)" 0 "$RO/ro-run.sh" $S
tail -1 "$LOG" | grep -q "RO-CHECK PASSED $S" && PASS=$((PASS+1)) && echo "ok   verdict line is last" || { FAIL=$((FAIL+1)); echo "FAIL verdict line"; }
reset; fsenv ntfs "$SRC ro"; rm -rf "$DST"; mkdir "$DST"; expect "ro-run with dest, fs warn-free" 0 "$RO/ro-run.sh" $S --dest "$DST"
reset; setv A197 2; expect "ro-run on suspect drive is WARN or FAIL, never PASS" 2 "$RO/ro-run.sh" $S --no-surface
reset; printf '99\n' > "$STUB/bad"; expect "ro-run with bad block fails" 1 "$RO/ro-run.sh" $S

# --- safety: nothing in the suite may use a write mode ---
if grep -n -P 'badblocks[^|]* -(w|n)[ s]|ddrescue[^|]*--force|mkfs|wipefs|fsck[^ ]* -[a-z]*[apy]|ntfsfix( |$)(?!.*--no-action)|dd +[^|]*of=/dev|smartctl[^|]*-t +(select|offline)' "$RO"/ro-*.sh "$RO/lib.sh" | grep -v '^[^:]*:[0-9]*: *#'; then
  FAIL=$((FAIL+1)); echo "FAIL write-capable command found"
else PASS=$((PASS+1)); echo "ok   no write-capable commands in the suite"; fi

echo; echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
