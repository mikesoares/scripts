#!/usr/bin/env python3
# Copyright (C) 2026 Michael Soares — GPL-3.0
"""
photo-db-scan.py — Scan photo directories, extract EXIF and filesystem dates,
store results in SQLite, and generate an analysis report.

Run this on the target server. No files are moved or renamed.

Usage:
    python3 photo-db-scan.py                          # full scan + report
    python3 photo-db-scan.py -v                       # verbose progress
    python3 photo-db-scan.py --db /tmp/photos.db      # custom DB path
    python3 photo-db-scan.py --roots /path/a /path/b  # override scan roots
    python3 photo-db-scan.py --report-only out.db     # report from existing DB

Requirements:
    exiftool  (apt install libimage-exiftool-perl)
    Python 3.6+, stdlib only
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone


# ============================================================
# Constants
# ============================================================

DEFAULT_SCAN_ROOTS = [
    '/mnt/tank/legacy/00-photos',
    '/mnt/tank/legacy/consolidated/Leo-Photos',
]

PHOTO_EXTENSIONS = frozenset({
    'jpg', 'jpeg', 'heic', 'heif', 'png', 'gif', 'bmp', 'webp',
    'tif', 'tiff', 'cr2', 'cr3', 'nef', 'arw', 'dng', 'rw2', 'orf', 'pef',
    'mp4', 'mov', 'avi', 'mpg', 'mpeg', 'mts', 'm2ts', 'wmv', 'mkv', '3gp',
})

# Valid year range for date prefix detection
_YEAR_PAT = r'(19[89]\d|20[0-3]\d)'


# ============================================================
# Database
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS directories (
    id               INTEGER PRIMARY KEY,
    path             TEXT NOT NULL UNIQUE,
    parent_path      TEXT,
    scan_root        TEXT NOT NULL,
    depth            INTEGER NOT NULL,
    raw_name         TEXT NOT NULL,
    date_prefix      TEXT,
    date_precision   TEXT,
    description      TEXT,
    file_count       INTEGER DEFAULT 0,
    photo_count      INTEGER DEFAULT 0,
    earliest_exif_date  TEXT,
    latest_exif_date    TEXT,
    earliest_file_date  TEXT,
    latest_file_date    TEXT,
    date_in_range    INTEGER,
    date_status      TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id               INTEGER PRIMARY KEY,
    dir_id           INTEGER REFERENCES directories(id),
    directory        TEXT NOT NULL,
    filename         TEXT NOT NULL,
    extension        TEXT,
    file_mtime       TEXT,
    exif_date        TEXT,
    exif_source      TEXT,
    date_conflict    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_files_dir_id      ON files(dir_id);
CREATE INDEX IF NOT EXISTS idx_files_directory   ON files(directory);
CREATE INDEX IF NOT EXISTS idx_dirs_path         ON directories(path);
CREATE INDEX IF NOT EXISTS idx_dirs_depth        ON directories(depth);
CREATE INDEX IF NOT EXISTS idx_dirs_status       ON directories(date_status);
"""


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ============================================================
# Date parsing
# ============================================================

# Matches canonical date prefix at the start of a directory name.
# Checked in order: full → month → year (longest match first).
_FULL_RE  = re.compile(r'^' + _YEAR_PAT + r'-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?:[\s_-]+(.+))?$')
_MONTH_RE = re.compile(r'^' + _YEAR_PAT + r'-(0[1-9]|1[0-2])(?:[\s_-]+(.+))?$')
_YEAR_RE  = re.compile(r'^' + _YEAR_PAT + r'(?:[\s_-]+(.+))?$')

# exiftool date format: "2009:07:03 14:25:36" (with optional timezone suffix)
_EXIF_DT_RE = re.compile(r'^(\d{4}):(\d{2}):(\d{2})(?:\s(\d{2}):(\d{2}):(\d{2}))?')


def parse_dir_date(name):
    """
    Extract a date prefix from a directory basename.
    Returns (date_prefix, precision, description) where precision is
    'full', 'month', or 'year', or (None, None, None) if no prefix found.
    """
    m = _FULL_RE.match(name)
    if m:
        y, mo, d, desc = m.group(1), m.group(2), m.group(3), m.group(4)
        return f'{y}-{mo}-{d}', 'full', (desc.strip() if desc else None)

    m = _MONTH_RE.match(name)
    if m:
        y, mo, desc = m.group(1), m.group(2), m.group(3)
        return f'{y}-{mo}', 'month', (desc.strip() if desc else None)

    m = _YEAR_RE.match(name)
    if m:
        y, desc = m.group(1), m.group(2)
        return y, 'year', (desc.strip() if desc else None)

    return None, None, None


def parse_exif_date(s):
    """Parse an exiftool date string into ISO format. Returns None on failure."""
    if not s:
        return None
    m = _EXIF_DT_RE.match(str(s))
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if y == '0000' or mo == '00' or d == '00':
        return None
    result = f'{y}-{mo}-{d}'
    if m.group(4):
        result += f'T{m.group(4)}:{m.group(5)}:{m.group(6)}'
    return result


def compute_date_in_range(prefix, precision, earliest, latest):
    """
    Check whether a directory's date prefix falls within the file date range.
    Compares at the granularity of the prefix (day/month/year).
    Returns 1, 0, or None if undetermined.
    """
    if not prefix or not earliest:
        return None
    e = earliest[:10]               # YYYY-MM-DD
    l = (latest or earliest)[:10]

    if precision == 'full':
        return 1 if e <= prefix <= l else 0
    if precision == 'month':
        return 1 if e[:7] <= prefix <= l[:7] else 0
    if precision == 'year':
        return 1 if e[:4] <= prefix <= l[:4] else 0
    return None


def compute_date_status(date_prefix, date_precision, file_count, photo_count, in_range):
    if file_count == 0:
        return 'container'   # organizational directory with no direct files
    if photo_count == 0:
        return 'no_photos'   # has files but none are recognized photos/videos
    if not date_prefix:
        return 'missing'
    if in_range == 0:
        return 'mismatch'
    if date_precision in ('month', 'year'):
        return 'partial'
    return 'ok'


# ============================================================
# EXIF extraction
# ============================================================

def run_exiftool(dirpath, verbose=False):
    """
    Run exiftool -json (non-recursive) on dirpath.
    Returns {filename: {'exif_date': str|None, 'exif_source': str|None, 'file_mtime': str|None}}.
    """
    cmd = [
        'exiftool', '-json', '-fast',
        '-DateTimeOriginal', '-CreateDate', '-FileModifyDate',
        dirpath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        if verbose:
            print(f'    [WARN] exiftool timed out: {dirpath}')
        return {}
    except FileNotFoundError:
        print('ERROR: exiftool not found. Install: apt install libimage-exiftool-perl', file=sys.stderr)
        sys.exit(1)

    if not result.stdout.strip():
        return {}

    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        if verbose:
            print(f'    [WARN] exiftool JSON parse error: {dirpath}')
        return {}

    out = {}
    for entry in entries:
        filepath = entry.get('SourceFile', '')
        if not filepath or os.path.isdir(filepath):
            continue
        filename = os.path.basename(filepath)

        exif_date = None
        exif_source = None
        for tag in ('DateTimeOriginal', 'CreateDate'):
            parsed = parse_exif_date(entry.get(tag))
            if parsed:
                exif_date = parsed
                exif_source = tag
                break

        file_mtime = parse_exif_date(entry.get('FileModifyDate'))
        out[filename] = {
            'exif_date': exif_date,
            'exif_source': exif_source,
            'file_mtime': file_mtime,
        }
    return out


# ============================================================
# Scanning
# ============================================================

def scan_root(conn, root_path, verbose=False, log_file=None):
    """Walk root_path and insert all directories and files into the DB."""

    def emit(msg=''):
        print(msg)
        if log_file:
            log_file.write(msg + '\n')

    # Collect all (dirpath, filenames) entries up front for a progress count.
    all_entries = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames.sort()
        all_entries.append((dirpath, sorted(filenames)))

    total = len(all_entries)
    emit(f'  {total} directories found.')

    cur = conn.cursor()
    photo_exts = list(PHOTO_EXTENSIONS)

    for i, (dirpath, filenames) in enumerate(all_entries, 1):
        rel = os.path.relpath(dirpath, root_path)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        raw_name = os.path.basename(dirpath)
        parent_path = os.path.dirname(dirpath) if dirpath != root_path else None
        date_prefix, date_precision, description = parse_dir_date(raw_name)

        if verbose:
            emit(f'  [{i:3d}/{total}] {dirpath}')
        elif i % 25 == 0 or i == total:
            emit(f'  [{i}/{total}] ...')

        cur.execute("""
            INSERT OR IGNORE INTO directories
                (path, parent_path, scan_root, depth, raw_name,
                 date_prefix, date_precision, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (dirpath, parent_path, root_path, depth, raw_name,
              date_prefix, date_precision, description))

        dir_id = cur.lastrowid
        if dir_id == 0:
            cur.execute('SELECT id FROM directories WHERE path = ?', (dirpath,))
            dir_id = cur.fetchone()[0]

        if not filenames:
            continue

        exif_data = run_exiftool(dirpath, verbose=verbose)

        file_rows = []
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else None

            info = exif_data.get(filename, {})
            exif_date   = info.get('exif_date')
            exif_source = info.get('exif_source')
            file_mtime  = info.get('file_mtime')

            # Filesystem mtime fallback if exiftool didn't return FileModifyDate
            if not file_mtime:
                try:
                    ts = os.path.getmtime(filepath)
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    file_mtime = dt.strftime('%Y-%m-%dT%H:%M:%S')
                except OSError:
                    pass

            conflict = None
            if exif_date and file_mtime:
                conflict = 0 if file_mtime[:10] == exif_date[:10] else 1

            file_rows.append((
                dir_id, dirpath, filename, ext,
                file_mtime, exif_date, exif_source, conflict,
            ))

        cur.executemany("""
            INSERT INTO files
                (dir_id, directory, filename, extension,
                 file_mtime, exif_date, exif_source, date_conflict)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, file_rows)

    conn.commit()
    emit(f'  Scan complete.')


# ============================================================
# Aggregation
# ============================================================

def aggregate(conn, verbose=False):
    """Compute per-directory stats from the files table and update directory rows."""
    cur = conn.cursor()
    cur.execute('SELECT id, path, date_prefix, date_precision FROM directories')
    dirs = cur.fetchall()

    photo_exts = list(PHOTO_EXTENSIONS)
    ph_placeholders = ','.join(['?'] * len(photo_exts))
    ph_clause = f'LOWER(extension) IN ({ph_placeholders})'

    for dir_id, path, date_prefix, date_precision in dirs:
        cur.execute('SELECT COUNT(*) FROM files WHERE dir_id = ?', (dir_id,))
        file_count = cur.fetchone()[0]

        cur.execute(
            f'SELECT COUNT(*) FROM files WHERE dir_id = ? AND {ph_clause}',
            [dir_id] + photo_exts,
        )
        photo_count = cur.fetchone()[0]

        cur.execute(
            f'SELECT MIN(exif_date), MAX(exif_date) FROM files '
            f'WHERE dir_id = ? AND exif_date IS NOT NULL AND {ph_clause}',
            [dir_id] + photo_exts,
        )
        earliest_exif, latest_exif = cur.fetchone()

        cur.execute(
            f'SELECT MIN(file_mtime), MAX(file_mtime) FROM files '
            f'WHERE dir_id = ? AND file_mtime IS NOT NULL AND {ph_clause}',
            [dir_id] + photo_exts,
        )
        earliest_mtime, latest_mtime = cur.fetchone()

        # Prefer EXIF dates for range check; fall back to mtime
        best_earliest = earliest_exif or earliest_mtime
        best_latest   = latest_exif or latest_mtime

        in_range = compute_date_in_range(date_prefix, date_precision, best_earliest, best_latest)
        status   = compute_date_status(date_prefix, date_precision, file_count, photo_count, in_range)

        cur.execute("""
            UPDATE directories SET
                file_count          = ?,
                photo_count         = ?,
                earliest_exif_date  = ?,
                latest_exif_date    = ?,
                earliest_file_date  = ?,
                latest_file_date    = ?,
                date_in_range       = ?,
                date_status         = ?
            WHERE id = ?
        """, (file_count, photo_count,
              earliest_exif, latest_exif,
              earliest_mtime, latest_mtime,
              in_range, status, dir_id))

    conn.commit()
    if verbose:
        print(f'  Aggregated {len(dirs)} directories.')


# ============================================================
# Reporting
# ============================================================

def report(conn, log_file=None):
    """Print the analysis report and optionally write it to log_file."""
    cur = conn.cursor()

    def emit(line=''):
        print(line)
        if log_file:
            log_file.write(line + '\n')

    def section(title):
        emit()
        emit('=' * 62)
        emit(f'  {title}')
        emit('=' * 62)

    photo_exts = list(PHOTO_EXTENSIONS)
    ph_placeholders = ','.join(['?'] * len(photo_exts))

    # ---- Summary ----
    section('SUMMARY')

    cur.execute('SELECT COUNT(*) FROM files')
    total_files = cur.fetchone()[0]
    cur.execute(f'SELECT COUNT(*) FROM files WHERE LOWER(extension) IN ({ph_placeholders})', photo_exts)
    total_photos = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM directories')
    total_dirs = cur.fetchone()[0]

    emit(f'  Total files:         {total_files:,}')
    emit(f'  Total photo files:   {total_photos:,}')
    emit(f'  Total directories:   {total_dirs:,}')
    emit()
    emit('  Directory status breakdown:')

    cur.execute("""
        SELECT date_status, COUNT(*) AS n
        FROM directories GROUP BY date_status ORDER BY n DESC
    """)
    for status, count in cur.fetchall():
        pct = 100.0 * count / total_dirs if total_dirs else 0
        emit(f'    {(status or "null"):<12}  {count:4d}  ({pct:.1f}%)')

    emit()
    emit('  Status key:')
    emit('    ok         — date prefix present, full precision, matches file dates')
    emit('    partial    — date prefix is YYYY-MM or YYYY only (not full YYYY-MM-DD)')
    emit('    missing    — no date prefix; has photo files')
    emit('    mismatch   — date prefix exists but falls outside the file date range')
    emit('    container  — organizational dir with no direct files (only subdirs)')
    emit('    no_photos  — has files but none are recognized photo/video formats')

    # ---- Missing date prefix ----
    section('MISSING DATE PREFIX')
    cur.execute("""
        SELECT path, depth, photo_count, file_count
        FROM directories WHERE date_status = 'missing'
        ORDER BY depth, path
    """)
    rows = cur.fetchall()
    emit(f'  {len(rows)} directories with no date prefix (have photo files):')
    emit()
    for path, depth, photos, files in rows:
        emit(f'  [d={depth}]  {path}')
        emit(f'         {photos} photos, {files} total files')

    # ---- Partial date ----
    section('PARTIAL DATE PREFIX  (YYYY-MM or YYYY only)')
    cur.execute("""
        SELECT path, depth, date_prefix, date_precision, photo_count,
               earliest_exif_date, latest_exif_date,
               earliest_file_date, latest_file_date
        FROM directories WHERE date_status = 'partial'
        ORDER BY date_prefix, path
    """)
    rows = cur.fetchall()
    emit(f'  {len(rows)} directories with partial date prefix:')
    emit()
    for path, depth, prefix, precision, photos, ee, le, em, lm in rows:
        best_e = (ee or em or '?')[:10]
        best_l = (le or lm or '?')[:10]
        emit(f'  [{precision:<5}]  {prefix}  →  {path}')
        emit(f'           files: {best_e} – {best_l}  ({photos} photos)')

    # ---- Date mismatch ----
    section('DATE MISMATCH  (prefix outside file date range)')
    cur.execute("""
        SELECT path, depth, date_prefix, date_precision, photo_count,
               earliest_exif_date, latest_exif_date,
               earliest_file_date, latest_file_date
        FROM directories WHERE date_status = 'mismatch'
        ORDER BY date_prefix, path
    """)
    rows = cur.fetchall()
    emit(f'  {len(rows)} directories where the date prefix conflicts with file dates:')
    emit()
    for path, depth, prefix, precision, photos, ee, le, em, lm in rows:
        emit(f'  {prefix} ({precision})  →  {path}')
        if ee or le:
            emit(f'    EXIF range:  {(ee or "?")[:10]} – {(le or "?")[:10]}')
        if em or lm:
            emit(f'    mtime range: {(em or "?")[:10]} – {(lm or "?")[:10]}')
        emit(f'    ({photos} photos)')

    # ---- Deep directories ----
    section('DEEP DIRECTORIES  (depth >= 3, candidates for flattening)')
    cur.execute("""
        SELECT path, depth, photo_count, file_count, date_status
        FROM directories WHERE depth >= 3
        ORDER BY depth DESC, path
    """)
    rows = cur.fetchall()
    emit(f'  {len(rows)} directories at depth 3 or deeper:')
    emit()
    for path, depth, photos, files, status in rows:
        emit(f'  [d={depth}] [{status:<9}]  {path}  ({photos} photos)')

    # ---- File date conflicts ----
    section('FILE DATE CONFLICTS  (EXIF date != filesystem mtime date)')
    cur.execute('SELECT COUNT(*) FROM files WHERE date_conflict = 1')
    conflict_count = cur.fetchone()[0]
    cur.execute(f'SELECT COUNT(*) FROM files WHERE date_conflict = 1 AND LOWER(extension) IN ({ph_placeholders})', photo_exts)
    photo_conflict_count = cur.fetchone()[0]

    emit(f'  {conflict_count:,} files have mismatched EXIF and mtime dates')
    emit(f'  ({photo_conflict_count:,} of those are photo/video files)')

    if conflict_count > 0:
        emit()
        emit('  Sample (up to 30):')
        cur.execute("""
            SELECT directory, filename, exif_date, file_mtime, exif_source
            FROM files WHERE date_conflict = 1
            ORDER BY directory, filename LIMIT 30
        """)
        for directory, filename, exif_dt, mtime, src in cur.fetchall():
            emit(f'    {os.path.join(directory, filename)}')
            emit(f'      EXIF ({src or "?"}): {(exif_dt or "?")[:10]}  |  mtime: {(mtime or "?")[:10]}')

    # ---- Container directories ----
    section('CONTAINER DIRECTORIES  (no direct files — organizational only)')
    cur.execute("""
        SELECT path, depth, date_prefix, date_precision
        FROM directories WHERE date_status = 'container'
        ORDER BY depth, path
    """)
    rows = cur.fetchall()
    emit(f'  {len(rows)} organizational directories with no direct files:')
    emit()
    for path, depth, prefix, precision in rows:
        prefix_label = f'{prefix} ({precision})' if prefix else 'NO PREFIX'
        emit(f'  [d={depth}]  [{prefix_label}]  {path}')

    # ---- No-photo directories ----
    section('NON-PHOTO DIRECTORIES  (files present but no photos/videos)')
    cur.execute("""
        SELECT path, depth, file_count
        FROM directories WHERE date_status = 'no_photos'
        ORDER BY depth, path
    """)
    rows = cur.fetchall()
    emit(f'  {len(rows)} directories contain only non-photo files:')
    emit()
    for path, depth, files in rows:
        emit(f'  [d={depth}]  {path}  ({files} non-photo files)')

    emit()
    emit('=' * 62)
    emit('  END OF REPORT')
    emit('=' * 62)
    emit()


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        description='Scan photo directories, extract dates, store in SQLite, report inconsistencies.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        '--db', metavar='PATH',
        help='Output SQLite database path (default: photo-scan-TIMESTAMP.db in script dir)',
    )
    p.add_argument(
        '--roots', nargs='+', metavar='PATH',
        help='Directories to scan (default: standard photo roots on 192.168.1.76)',
    )
    p.add_argument(
        '--report-only', metavar='DB_PATH',
        help='Skip scanning; re-aggregate and report from an existing database',
    )
    p.add_argument(
        '-v', '--verbose', action='store_true',
        help='Print every directory as it is processed',
    )
    return p


# ============================================================
# Main
# ============================================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    if args.report_only:
        db_path  = args.report_only
        log_path = os.path.join(script_dir, f'photo-scan-report-{ts}.log')
    else:
        db_path  = args.db or os.path.join(script_dir, f'photo-scan-{ts}.db')
        log_path = os.path.join(script_dir, f'photo-scan-{ts}.log')

    roots = args.roots or DEFAULT_SCAN_ROOTS

    print(f'=== Photo Directory Scanner ===')
    print(f'DB:   {db_path}')
    print(f'Log:  {log_path}')

    with open(log_path, 'w', encoding='utf-8') as log:
        log.write(f'=== Photo Directory Scanner — {datetime.now().isoformat()} ===\n')
        log.write(f'DB:   {db_path}\n\n')

        conn = init_db(db_path)

        if not args.report_only:
            for i, root in enumerate(roots, 1):
                msg = f'\n[{i}/{len(roots)}] Scanning {root} ...'
                print(msg); log.write(msg + '\n')

                if not os.path.isdir(root):
                    warn = f'  WARNING: {root} does not exist — skipping.'
                    print(warn); log.write(warn + '\n')
                    continue

                scan_root(conn, root, verbose=args.verbose, log_file=log)

        print('\nAggregating directory stats...')
        log.write('\nAggregating...\n')
        aggregate(conn, verbose=args.verbose)

        print('Generating report...\n')
        log.write('\n--- Report ---\n')
        report(conn, log_file=log)

        conn.close()

    print(f'DB:   {db_path}')
    print(f'Log:  {log_path}')


if __name__ == '__main__':
    main()
