#!/bin/bash
# One-shot status of a running read-only queue (ro-queue.sh or ro-run.sh): current stage,
# self-test or scan progress, temperature, SMART counters now versus the baseline, kernel
# I/O errors since the run started, and the device's sectors-written counter (should stay 0).
# Only reads. Safe to run repeatedly, e.g. from cron or a notifier, for periodic updates.
# Usage: ./ro-status.sh <serial>
# Env:   SYS_BLOCK  sysfs block directory (default /sys/block; overridden by the tests)
DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"
STEP=status
ro_init "${1:-}" >/dev/null
detect_smart
BASE=$SNAPDIR/baseline.attrs
echo "now: $(date '+%F %T %Z')   queue started: $(date -d @"$(cat "$SNAPDIR/started" 2>/dev/null)" '+%F %T' 2>/dev/null)"
echo "stage: $(grep -E '(queue|run): --- ' "$LOG" | tail -1 | sed 's/.*--- \(.*\) ---/\1/')   verdict: $(grep -o -E 'RO-CHECK [A-Z]+ '"$SERIAL" "$LOG" | tail -1)"
echo "screen: $(screen -ls 2>/dev/null | grep -c "ro-$SERIAL") session(s)"
# self-test progress, or the last badblocks progress line
ST=$(smart -c | grep -A1 'Self-test routine in progress' | head -2 | tr -s ' \t' ' ')
[ -n "$ST" ] && echo "self-test: $ST"
BB=$(tr '\b\r' '\n\n' < "$LOG" | grep -E 'done, [0-9:]+ elapsed' | tail -1)
[ -n "$BB" ] && echo "surface scan: $BB"
echo "health: $(health)"
echo "temp: $(smart -x | awk '/^Current Temperature:/{c=$3} /^Power Cycle Min\/Max/{p=$5} /^Lifetime +Min\/Max/{l=$4} END{print c" C now; since power-on min/max "p" C; lifetime "l" C"}')"
echo "SMART counters (now / baseline):"
for a in 5 187 188 196 197 198 199; do
  printf '  attr %-3s %s / %s\n' "$a" "$(attr_raw $a)" "$(attr_get "$BASE" attr_$a)"
done
echo "  ATA error log entries: $(errlog_count) / $(attr_get "$BASE" errlog)"
echo "  raw read error rate (attr 1; huge values are normal on some vendors): $(attr_raw 1)"
echo "sectors written to device (must stay 0): $(awk '{print $7}' ${SYS_BLOCK:-/sys/block}/$DEVNAME/stat), read-only flag: $($SUDO blockdev --getro $DEV)"
SINCE=$(cat "$SNAPDIR/started" 2>/dev/null)
K=$($SUDO journalctl -k --no-pager --since "@$SINCE" 2>/dev/null | grep -E "\b$DEVNAME\b.*(I/O error|timeout)|ata[0-9]+(\.[0-9]+)?: .*(hard resetting link|failed command|SError)" | tail -3)
echo "kernel I/O errors or link resets since start: ${K:-none}"
echo "bad blocks found so far: $(grep -c -E '^[0-9]+$' "$SNAPDIR/surface-badblocks.txt" 2>/dev/null || echo 0)"
