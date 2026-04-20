# Photo Organization

Pipeline for reorganizing a photo archive. Run the scripts in numbered order.

## Pipeline

1. **`01-fix-timestamps.sh`** — restore file mtimes from EXIF
2. **`02-organize-dirs.py`** — normalize directory names to canonical format
3. **`03-scan.py`** — scan and analyze the cleaned directory tree
4. **`04-rename-dirs.py`** — apply date-based renames using the scan DB

Both `02-organize-dirs.py` and `04-rename-dirs.py` default to dry run — pass `--live` when ready to execute.

## Scripts

### 01-fix-timestamps.sh

Overwrites file mtimes with each photo's EXIF `DateTimeOriginal` (falls back to `CreateDate`). Useful for restoring accurate timestamps on photos that were copied or transferred without preserving metadata. Skips files with no EXIF date.

**Requirements:** `exiftool` (`apt install libimage-exiftool-perl`). No other dependencies.

**Usage:**

```bash
sudo bash 01-fix-timestamps.sh             # Live run
sudo bash 01-fix-timestamps.sh --dry-run   # Preview changes without modifying files
```

The script is hardcoded to `/mnt/tank/legacy/00-photos-to-resolve` and excludes the `2011-11 - Europe` subfolder. Edit `PHOTOS_DIR` and `EXCLUDE_DIR` at the top of the script to target a different path.

Logs all actions (updates and EXIF-less skips) to `/tmp/photo-timestamp-fix.log`.

**Supported formats:** JPG/JPEG, HEIC/HEIF, PNG, TIFF, CR2, CR3, NEF, ARW, DNG, RW2.

### 02-organize-dirs.py

Recursively renames photo directories to a canonical `YYYY[-MM[-DD]] - Description` format. Processes bottom-up so parent renames never break child paths. Defaults to dry run; pass `--live` to execute.

**Requirements:** Python 3.6+. No external dependencies — stdlib only.

**Usage:**

```bash
python3 02-organize-dirs.py <root_dir> [--live] [--exclude DIR ...]
```

```bash
python3 02-organize-dirs.py /mnt/tank/photos              # Dry run
python3 02-organize-dirs.py /mnt/tank/photos --live       # Execute renames
python3 02-organize-dirs.py /mnt/tank/photos --live \
  --exclude "Mediterranean Cruise 2009" \
  --exclude "Vacation 2018"                               # Skip subtrees by basename
```

**CLI flags:**

| Flag | Description |
|------|-------------|
| `--live` | Execute renames (default: dry run — no changes made) |
| `--exclude DIR` | Skip a directory and its subtree; rename the dir itself but not its contents. Matched by basename. Repeatable. |

**What it normalizes:**

| Input | Output |
|-------|--------|
| `Eurotrip 2008 Pics` | `2008 - Eurotrip Pics` |
| `2008-04-27, Cynthia's Bridal Shower` | `2008-04-27 - Cynthia's Bridal Shower` |
| `2019_Michael's Wedding` | `2019 - Michael's Wedding` |
| `Jania's First Birthday (2004)` | `2004 - Jania's First Birthday` |

**Skip conditions:** ambiguous date formats (e.g. `08.08.02`), years outside 2000–2026, multiple conflicting dates in one name, rename target already exists.

Logs every rename, skip, and exclusion to `<script_dir>/photo-organizer-YYYYMMDD_HHMMSS.log`.

### 03-scan.py

Scans photo directories, extracts EXIF and filesystem dates, stores everything in a SQLite database, and generates a structured analysis report. Read-only — no files are moved.

**Requirements:** Python 3.6+, `exiftool` (`apt install libimage-exiftool-perl`). No external Python dependencies — stdlib only.

**Usage:**

```bash
python3 03-scan.py                          # Full scan + report
python3 03-scan.py -v                       # Verbose (prints each directory)
python3 03-scan.py --db /tmp/photos.db      # Custom DB path
python3 03-scan.py --roots /path/a /path/b  # Override scan roots
python3 03-scan.py --report-only out.db     # Re-run report on existing DB
```

**CLI flags:**

| Flag | Description |
|------|-------------|
| `--db PATH` | Output SQLite database path (default: `photo-scan-TIMESTAMP.db` in script dir) |
| `--roots PATH ...` | Override scan root directories |
| `--report-only DB_PATH` | Skip scan; re-aggregate and report from an existing DB |
| `-v`, `--verbose` | Print every directory as it is processed |

**What it captures:**

- Per-file: directory, filename, extension, filesystem mtime, EXIF date (`DateTimeOriginal` → `CreateDate`), whether the two dates conflict
- Per-directory: depth from scan root, extracted date prefix (YYYY-MM-DD / YYYY-MM / YYYY) and description, date range of contained photos, whether the prefix matches the file date range

**Directory status values in the report:**

| Status | Meaning |
|--------|---------|
| `ok` | Full YYYY-MM-DD prefix present and matches file dates |
| `partial` | Prefix is YYYY-MM or YYYY only |
| `missing` | No date prefix; directory contains photos |
| `mismatch` | Prefix exists but falls outside the file date range |
| `container` | Organizational directory with no direct files (only subdirs) |
| `no_photos` | Contains files but none are recognized photo/video formats |

Outputs `photo-scan-TIMESTAMP.db` and `photo-scan-TIMESTAMP.log` to the script directory.

### 04-rename-dirs.py

Reads a scan DB produced by `03-scan.py` and renames all depth=1 non-container directories in the two photo roots based on their EXIF date range. Defaults to dry run; pass `--live` to execute.

**Requirements:** Python 3.6+. No external dependencies — stdlib only.

**Usage:**

```bash
python3 04-rename-dirs.py --db SCAN.db            # Dry run (preview only)
python3 04-rename-dirs.py --db SCAN.db --live     # Execute renames
python3 04-rename-dirs.py --db SCAN.db --live -v  # Execute + verbose
python3 04-rename-dirs.py --db SCAN.db --exclude "My Album"  # Skip by name
```

**CLI flags:**

| Flag | Description |
|------|-------------|
| `--db PATH` | Path to scan DB produced by `03-scan.py` (required) |
| `--live` | Execute renames (default: dry run — no changes made) |
| `--exclude NAME` | Additional directory basename to exclude. Repeatable. |
| `-v`, `--verbose` | Print every directory as it is evaluated |

**Rename rules (applied to each depth=1 non-container directory):**

| Condition | New name |
|-----------|----------|
| `date_status = no_photos` | `NO PHOTOS - <description>` |
| No EXIF dates in any file | `NO EXIF - <description>` |
| EXIF date range > 30 days | `DUMP - <description>` |
| EXIF date range ≤ 30 days | `YYYY-MM-DD - <description>` (from earliest EXIF date) |

The description is taken from the DB's parsed description field (date prefix already stripped). If the DB description is NULL (directory had no date prefix when scanned), the original directory name is used as-is.

**Hardcoded exclusions:** `2011-11 - Italy`

**Re-running:** Running against the same DB after executing renames is unsupported — run a fresh `03-scan.py` scan first.

Outputs `photo-rename-TIMESTAMP.log` to the script directory.

## Project Structure

```
photo-organization/
├── 01-fix-timestamps.sh         # Restore file mtimes from EXIF DateTimeOriginal
├── 02-organize-dirs.py          # Normalize photo directory names to YYYY - Description format
├── 03-scan.py                   # Scan photo dirs, extract EXIF/dates, report inconsistencies
├── 04-rename-dirs.py            # Rename depth=1 photo dirs based on EXIF date range from scan DB
├── photo-scan-TIMESTAMP.db      # Runtime — scan database (auto-created by 03-scan.py, gitignored)
├── photo-scan-TIMESTAMP.log     # Runtime — scan log (auto-created by 03-scan.py, gitignored)
├── photo-rename-TIMESTAMP.log   # Runtime — rename log (auto-created by 04-rename-dirs.py, gitignored)
└── photo-organizer-*.log        # Runtime — organize log (auto-created by 02-organize-dirs.py, gitignored)
```
