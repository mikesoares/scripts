#!/bin/bash
# Launch a screen session to run a drive test action against a single drive
# Usage: ./drive-test-phase1.sh <badblocks|smart-status|smart-long> <drive>
# Example: ./drive-test-phase1.sh badblocks sdg

MODE=${1:-}
DRIVE=${2:-}

if [ -z "$MODE" ] || [ -z "$DRIVE" ]; then
  echo "Usage: $0 <badblocks|smart-status|smart-long> <drive>"
  echo "Example: $0 badblocks sdg"
  exit 1
fi

case $MODE in
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
    echo "Usage: $0 <badblocks|smart-status|smart-long> <drive>"
    exit 1
    ;;
esac

screen -dmS "$SESSION"
screen -S "$SESSION" -p 0 -X title "$DRIVE"
screen -S "$SESSION" -p 0 -X stuff "$CMD\n"

echo "Session '$SESSION' started for /dev/$DRIVE"
echo "Attach with: screen -r $SESSION"
echo "Detach: Ctrl-A d"
