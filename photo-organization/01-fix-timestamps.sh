#!/bin/bash
# Overwrites file mtime with EXIF DateTimeOriginal (falls back to CreateDate).
# Skips files with no EXIF date. Excludes "2011-11 - Europe" subfolder.
# Usage: fix-photo-timestamps.sh [--dry-run]

set -euo pipefail

PHOTOS_DIR="/mnt/tank/legacy/00-photos-to-resolve"
EXCLUDE_DIR="2011-11 - Europe"
LOG_FILE="/tmp/photo-timestamp-fix.log"
DRY_RUN="${1:-}"

echo "Photo timestamp fix — $(date)" | tee "$LOG_FILE"
echo "Target:    $PHOTOS_DIR" | tee -a "$LOG_FILE"
echo "Excluding: $EXCLUDE_DIR" | tee -a "$LOG_FILE"
[[ "$DRY_RUN" == "--dry-run" ]] && echo "Mode: DRY RUN" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

count_updated=0
count_no_exif=0

while IFS= read -r -d '' file; do
    # DateTimeOriginal = shutter time; CreateDate = digitized time (fallback)
    exif_date=$(exiftool -s3 -d "%Y%m%d%H%M.%S" -DateTimeOriginal "$file" 2>/dev/null)

    if [[ -z "$exif_date" ]]; then
        exif_date=$(exiftool -s3 -d "%Y%m%d%H%M.%S" -CreateDate "$file" 2>/dev/null)
    fi

    if [[ -z "$exif_date" ]]; then
        echo "NO EXIF: $file" | tee -a "$LOG_FILE"
        ((count_no_exif++)) || true
        continue
    fi

    echo "UPDATE:  $file  ->  $exif_date" | tee -a "$LOG_FILE"
    ((count_updated++)) || true

    if [[ "$DRY_RUN" != "--dry-run" ]]; then
        touch -t "$exif_date" "$file"
    fi

done < <(find "$PHOTOS_DIR" \
    -not -path "*/${EXCLUDE_DIR}/*" \
    -not -path "*/${EXCLUDE_DIR}" \
    -type f \
    \( -iname "*.jpg" -o -iname "*.jpeg" \
       -o -iname "*.heic" -o -iname "*.heif" \
       -o -iname "*.png" \
       -o -iname "*.tif" -o -iname "*.tiff" \
       -o -iname "*.cr2" -o -iname "*.cr3" \
       -o -iname "*.nef" -o -iname "*.arw" \
       -o -iname "*.dng" -o -iname "*.rw2" \) \
    -print0)

echo "" | tee -a "$LOG_FILE"
echo "Done." | tee -a "$LOG_FILE"
echo "  Updated:  $count_updated" | tee -a "$LOG_FILE"
echo "  No EXIF:  $count_no_exif" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Full log: $LOG_FILE"
