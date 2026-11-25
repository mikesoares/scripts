#!/bin/bash
# Record a SMART snapshot of one drive: identity, health, key attributes, error log.
# Read-only: only SMART queries are sent.
# Usage: ./ro-baseline.sh <serial> [label]
# Example: ./ro-baseline.sh ABC123XYZ            # label "baseline" (the reference snapshot)
#          ./ro-baseline.sh ABC123XYZ after      # later snapshot, diffed by ro-compare.sh
#
# Output: $LOGDIR/<serial>-ro-snapshots/<label>.txt   full `smartctl -x`
#         $LOGDIR/<serial>-ro-snapshots/<label>.attrs key=value summary used for comparisons
# The "baseline" label also records the start time ($SNAPDIR/started, epoch seconds) that
# ro-compare.sh uses to scope the kernel-log scan.
#
# Exit: 0 clean, 2 WARN (suspect drive: see `suspect=yes` in the .attrs file),
#       3 no SMART available.

DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib.sh
. "$DIR/lib.sh"

STEP=baseline
ro_init "${1:-}"
LABEL=${2:-baseline}

if ! detect_smart; then
  log "SKIPPED: $DEV ($SERIAL) offers no SMART (tried native and -d sat); remaining checks still work"
  echo "smart=none" > "$SNAPDIR/$LABEL.attrs"
  [ "$LABEL" = baseline ] && date +%s > "$SNAPDIR/started"
  exit $RC_SKIP
fi

[ "$LABEL" = baseline ] && date +%s > "$SNAPDIR/started"
smart -x > "$SNAPDIR/$LABEL.txt" 2>&1
ATTRS=$SNAPDIR/$LABEL.attrs

H=$(health)
{
  echo "smart=ok"
  echo "health=$H"
  echo "model=$(smart -i | sed -n 's/^\(Device Model\|Model Number\|Product\): *//p' | head -1)"
  echo "wwn=$(smart -i | sed -n 's/^LU WWN Device Id: *//p' | head -1)"
  if is_nvme; then
    echo "media_errors=$(nvme_field 'Media and Data Integrity Errors')"
    echo "critical_warning=$(nvme_field 'Critical Warning')"
    echo "percentage_used=$(nvme_field 'Percentage Used')"
    echo "poh=$(nvme_field 'Power On Hours')"
  else
    # 5 reallocated, 187 reported uncorrect, 188 command timeout, 196 reallocation events,
    # 197 pending, 198 offline uncorrectable, 199 interface CRC, 9 power-on hours,
    # 12 power cycles, 193 load cycles. SSDs also expose wear/reserve under 177/231/232/233
    # (normalised value, 100 = new): recorded as wear_<id> for the report.
    for a in 5 9 12 187 188 193 196 197 198 199; do echo "attr_$a=$(attr_raw $a)"; done
    for a in 177 231 232 233; do echo "wear_$a=$(attr_norm $a)"; done
    echo "errlog=$(errlog_count)"
  fi
} > "$ATTRS"

# Suspect = anything that says "this drive is already failing or has failed before".
SUSPECT=no
REASONS=""
[ "$H" = PASSED ] || { SUSPECT=yes; REASONS="$REASONS health=$H"; }
for a in 5 197 198; do
  v=$(attr_get "$ATTRS" "attr_$a")
  [ -n "$v" ] && [ "$v" != 0 ] && { SUSPECT=yes; REASONS="$REASONS attr$a=$v"; }
done
e=$(attr_get "$ATTRS" errlog)
[ -n "$e" ] && [ "$e" != 0 ] && [ "$e" != '?' ] && { SUSPECT=yes; REASONS="$REASONS errlog=$e"; }
m=$(attr_get "$ATTRS" media_errors)
[ -n "$m" ] && [ "$m" != 0 ] && { SUSPECT=yes; REASONS="$REASONS media_errors=$m"; }
echo "suspect=$SUSPECT" >> "$ATTRS"

log "$LABEL snapshot of $DEV ($SERIAL): $(attr_get "$ATTRS" model), health=$H, power-on hours=$(attr_get "$ATTRS" attr_9)$(attr_get "$ATTRS" poh)"
if [ "$SUSPECT" = yes ]; then
  log "WARN: drive is suspect:$REASONS"
  exit $RC_WARN
fi
log "no bad-sector indicators"
exit $RC_PASS
