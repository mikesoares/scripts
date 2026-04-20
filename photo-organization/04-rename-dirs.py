#!/usr/bin/env python3
# Copyright (C) 2026 Michael Soares — GPL-3.0
"""
photo-rename-dirs.py — Rename depth=1 and depth=2 photo directories based on EXIF
date range data from a photo-db-scan.py SQLite database.

Classifies each non-container directory:
  - date range >30 days     → DUMP - <description>
  - date range <=30 days    → YYYY-MM-DD - <description>
  - no_photos status        → NO PHOTOS - <description>
  - no EXIF dates available → NO EXIF - <description>

Processes depth=2 before depth=1 so parent renames never invalidate child paths.
Children of excluded directories are automatically excluded.

Defaults to dry run. Pass --live to execute renames.

Re-running against the same DB after executing renames is unsupported.
Run a fresh photo-db-scan.py scan before re-running this script.

Usage:
    python3 photo-rename-dirs.py --db SCAN.db            # dry run
    python3 photo-rename-dirs.py --db SCAN.db --live     # execute
    python3 photo-rename-dirs.py --db SCAN.db --live -v  # execute + verbose
    python3 photo-rename-dirs.py --db SCAN.db --exclude "My Album"  # skip by name
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

# === Constants ===================================================

DUMP_THRESHOLD_DAYS = 30

HARDCODED_EXCLUDES = {
    '2011-11 - Italy',
    '2009-09-11 - Mediterranean Cruise',
}

SKIP_PREFIXES = {
    'Collection - ',
}

# === Date Utilities ==============================================

def date_range_days(earliest, latest):
    e = datetime.strptime(earliest[:10], '%Y-%m-%d').date()
    l = datetime.strptime((latest or earliest)[:10], '%Y-%m-%d').date()
    return (l - e).days


# === Core Logic ==================================================

def process_dirs(conn, excludes, dry_run, verbose, emit):
    rows = conn.execute("""
        SELECT path, parent_path, raw_name, description, date_status,
               date_prefix, earliest_exif_date, latest_exif_date
        FROM directories
        WHERE depth IN (1, 2) AND date_status != 'container'
        ORDER BY depth DESC, path
    """).fetchall()

    counts = {'renamed': 0, 'no_change': 0, 'skipped': 0, 'missing': 0}

    for path, parent_path, raw_name, description, date_status, date_prefix, earliest_exif, latest_exif in rows:

        parent_name = os.path.basename(parent_path) if parent_path else None
        if raw_name in excludes or parent_name in excludes:
            if verbose:
                emit(f'  EXCLUDE  {path}')
            counts['skipped'] += 1
            continue

        if any(raw_name.startswith(pfx) for pfx in SKIP_PREFIXES):
            if verbose:
                emit(f'  SKIP     {path}  (already has skip prefix)')
            counts['skipped'] += 1
            continue

        if not os.path.exists(path):
            emit(f'  MISSING  {path}  (not found on disk — skipped)')
            counts['missing'] += 1
            continue

        # Determine description: prefer DB description, fall back to raw_name
        # Strip any previously-applied special prefix from raw_name to avoid
        # double-prefixing (e.g. "DUMP - X" re-classified as "DUMP - DUMP - X")
        if description:
            desc = description
        else:
            for pfx in ('DUMP - ', 'NO EXIF - ', 'NO PHOTOS - '):
                if raw_name.startswith(pfx):
                    desc = raw_name[len(pfx):]
                    break
            else:
                desc = raw_name

        # Determine new prefix
        if date_status == 'no_photos':
            new_prefix = 'NO PHOTOS'
        elif not earliest_exif:
            if date_prefix:
                if verbose:
                    emit(f'  SKIP     {path}  (no EXIF but has existing date prefix)')
                counts['skipped'] += 1
                continue
            new_prefix = 'NO EXIF'
        else:
            days = date_range_days(earliest_exif, latest_exif)
            if days > DUMP_THRESHOLD_DAYS:
                new_prefix = 'DUMP'
            else:
                new_prefix = earliest_exif[:10]  # YYYY-MM-DD

        new_name = f'{new_prefix} - {desc}'
        new_path = os.path.join(os.path.dirname(path), new_name)

        if new_name == raw_name:
            if verbose:
                emit(f'  OK       {path}')
            counts['no_change'] += 1
            continue

        if dry_run:
            emit(f'  DRY-RUN  {path}')
            emit(f'        →  {new_path}')
        else:
            try:
                os.rename(path, new_path)
                emit(f'  RENAMED  {path}')
                emit(f'        →  {new_path}')
            except OSError as exc:
                emit(f'  ERROR    {path}  ({exc})')
                counts['skipped'] += 1
                continue

        counts['renamed'] += 1

    return counts


# === CLI =========================================================

def build_parser():
    p = argparse.ArgumentParser(
        description='Rename depth=1 and depth=2 photo directories based on EXIF date range from a scan DB.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('--db', metavar='PATH', required=True,
                   help='Path to scan DB produced by photo-db-scan.py')
    p.add_argument('--live', action='store_true',
                   help='Execute renames (default: dry run — no changes made)')
    p.add_argument('--exclude', metavar='NAME', action='append', dest='excludes', default=[],
                   help='Additional directory basename to exclude. Repeatable.')
    p.add_argument('-v', '--verbose', action='store_true',
                   help='Print every directory as it is evaluated')
    return p


# === Main ========================================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f'ERROR: DB not found: {args.db}', file=sys.stderr)
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(script_dir, f'photo-rename-{ts}.log')

    excludes = HARDCODED_EXCLUDES | set(args.excludes)
    dry_run = not args.live

    conn = sqlite3.connect(args.db)

    with open(log_path, 'w', encoding='utf-8') as log:

        def emit(msg=''):
            print(msg, flush=True)
            log.write(msg + '\n')
            log.flush()

        emit(f'=== Photo Directory Rename — {datetime.now().isoformat()} ===')
        emit(f'DB:       {args.db}')
        emit(f'Mode:     {"DRY RUN" if dry_run else "LIVE"}')
        if args.excludes:
            emit(f'Excludes: {sorted(args.excludes)} (+ hardcoded)')
        emit()

        counts = process_dirs(conn, excludes, dry_run, args.verbose, emit)

        emit()
        emit('=== Summary ===')
        if dry_run:
            emit(f'  Would rename:  {counts["renamed"]}')
        else:
            emit(f'  Renamed:       {counts["renamed"]}')
        emit(f'  No change:     {counts["no_change"]}')
        emit(f'  Skipped:       {counts["skipped"]}')
        if counts['missing']:
            emit(f'  Path missing:  {counts["missing"]}')
        emit()
        emit(f'Log written to: {log_path}')

    conn.close()


if __name__ == '__main__':
    main()
