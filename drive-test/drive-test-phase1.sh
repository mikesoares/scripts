#!/bin/bash
# Launch a screen session to run a drive test action against a single drive
# Usage: ./drive-test-phase1.sh <precheck|badblocks|smart-status|smart-long> <drive>
# Example: ./drive-test-phase1.sh badblocks sdg

MODE=${1:-}
DRIVE=${2:-}

# Internal mode: the precheck body, run inside the screen session. Kept as real
# script code (not an inline screen command) because `screen -X stuff` expands
# $VARS and treats ^X as a control character, which mangles awk/grep filters.
if [ "$MODE" = "_precheck" ]; then
  echo "=== /dev/$DRIVE ==="
  sudo smartctl -i "/dev/$DRIVE"
  sudo smartctl -A "/dev/$DRIVE"
  echo "--- non-zero bad-sector attrs (5/197/198), empty is good ---"
  # Raw value is the last column; a line not ending in " 0" is non-zero.
  sudo smartctl -A "/dev/$DRIVE" | grep -E '^ *(5|197|198) ' | grep -v -E ' 0$'
  exit 0
fi

if [ -z "$MODE" ] || [ -z "$DRIVE" ]; then
  echo "Usage: $0 <precheck|badblocks|smart-status|smart-long> <drive>"
  echo "Example: $0 badblocks sdg"
  exit 1
fi

case $MODE in
  precheck)
    # Identity (wwn/serial) + SMART attributes, saved so results can be tied to a physical disk.
    # Non-zero 5/197/198 raw values mean the drive is already bad.
    SESSION="precheck-$DRIVE"
    CMD="bash $(readlink -f "$0") _precheck $DRIVE 2>&1 | tee /tmp/$DRIVE-precheck.log"
    ;;
  badblocks)
    SESSION="drivetest-$DRIVE"
    CMD="echo '=== Badblocks /dev/$DRIVE ===' && sudo badblocks -b 4096 -c 65535 -wsv /dev/$DRIVE 2>&1 | tee /tmp/$DRIVE-badblocks.log"
    ;;
  smart-status)
    SESSION="smart-status-$DRIVE"
    CMD="echo '=== /dev/$DRIVE ===' && sudo smartctl -A /dev/$DRIVE && sudo smartctl -l selftest /dev/$DRIVE | head -10"
    ;;
  smart-long)
    SESSION="smart-long-$DRIVE"
    CMD="sudo smartctl -t long /dev/$DRIVE"
    ;;
  *)
    echo "Unknown mode: $MODE"
    echo "Usage: $0 <precheck|badblocks|smart-status|smart-long> <drive>"
    exit 1
    ;;
esac

screen -dmS "$SESSION"
# Give the shell in the new session time to start; input stuffed before it is up is silently lost.
sleep 1
screen -S "$SESSION" -p 0 -X title "$DRIVE"
screen -S "$SESSION" -p 0 -X stuff "$CMD\n"

echo "Session '$SESSION' started for /dev/$DRIVE"
echo "Attach with: screen -r $SESSION"
echo "Detach: Ctrl-A d"
