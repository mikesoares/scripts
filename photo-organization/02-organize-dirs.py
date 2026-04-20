#!/usr/bin/env python3
"""
Photo directory organizer.

Recursively renames directories bottom-up to the canonical format:
    YYYY[-MM[-DD]] - Description

Usage:
    python3 organize-photo-dirs.py <root_dir> [--live] [--exclude DIR ...]

Defaults to dry run. Pass --live to execute renames.
--exclude skips a directory and its entire subtree (matched by basename).
Log is written to the script's directory with a timestamp suffix.
"""

import argparse
import os
import re
import sys
from datetime import datetime


# ============================================================
# Patterns
# ============================================================

# Matches YYYY-MM-DD, YYYY-MM, or YYYY for years 2000–2026.
# Greedy alternation ensures longest match wins.
DATE_RE = re.compile(
    r'(?<!\d)'
    r'(20[01]\d|202[0-6])'
    r'(?:-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?)?'
    r'(?!\d)'
)

# Ambiguous 2-digit date patterns to skip (e.g. 08.08.02)
AMBIGUOUS_RE = re.compile(r'\b\d{2}[.\-/]\d{2}[.\-/]\d{2}\b')

# Any standalone 4-digit number — used to catch out-of-range years
FOUR_DIGIT_RE = re.compile(r'\b(\d{4})\b')

# Empty parentheses left behind after date extraction
EMPTY_PARENS_RE = re.compile(r'\(\s*\)')


# ============================================================
# Name processing
# ============================================================

def _clean_remainder(s):
    """Normalize text after removing a date: strip separators, collapse spaces."""
    s = EMPTY_PARENS_RE.sub('', s)
    s = re.sub(r'\s+', ' ', s)
    s = s.strip()
    s = s.strip(' \t-_')
    s = s.strip()
    return s


def compute_new_name(basename):
    """
    Return (new_name, skip_reason).

    new_name     — the proposed new basename, or None if no rename is needed
    skip_reason  — non-empty string if the directory should be skipped
    """
    # Step 1: ambiguous date format (e.g. 08.08.02)
    if AMBIGUOUS_RE.search(basename):
        return None, 'ambiguous date format'

    # Step 2: out-of-range 4-digit year
    for raw in FOUR_DIGIT_RE.findall(basename):
        year = int(raw)
        if year < 2000 or year > 2026:
            return None, f'out-of-range year ({year})'

    # Step 3: comma normalization  (", " or "," → " - ")
    name = re.sub(r',\s*', ' - ', basename)
    name = re.sub(r'\s+', ' ', name).strip()

    # Step 4: find all date matches in the post-comma name
    matches = list(DATE_RE.finditer(name))

    # Step 5: conflicting dates
    if len(matches) > 1:
        return None, 'conflicting dates'

    # Step 6: no date found
    if len(matches) == 0:
        if name != basename:
            return name, None   # comma was replaced; return cleaned form
        return None, None       # nothing to do

    # Exactly one date match — reposition if needed
    m = matches[0]
    date_str = m.group(0)
    start, end = m.span()

    before = name[:start]
    after = name[end:]
    remainder = _clean_remainder(before + ' ' + after)

    new_name = f'{date_str} - {remainder}' if remainder else date_str

    if new_name == basename:
        return None, None   # already canonical

    return new_name, None


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Normalize photo directory names to YYYY[-MM[-DD]] - Description.'
    )
    parser.add_argument('root_dir', help='Root directory to process recursively')
    parser.add_argument(
        '--live', action='store_true',
        help='Execute renames (default: dry run — no changes made)'
    )
    parser.add_argument(
        '--exclude', action='append', metavar='DIR', dest='excludes', default=[],
        help='Skip this directory and its entire subtree (matched by basename). Repeatable.'
    )
    args = parser.parse_args()

    root_dir = os.path.abspath(args.root_dir)
    dry_run = not args.live
    exclude_set = set(args.excludes)

    if not os.path.isdir(root_dir):
        print(f'Error: {root_dir!r} is not a directory', file=sys.stderr)
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(script_dir, f'photo-organizer-{timestamp}.log')

    mode_label = 'DRY RUN' if dry_run else 'LIVE'
    header = [
        f'Photo Directory Organizer — {mode_label}',
        f'Root:    {root_dir}',
    ]
    if exclude_set:
        header.append(f'Exclude: {", ".join(sorted(exclude_set))}')
    header += [
        f'Started: {datetime.now().isoformat(timespec="seconds")}',
        '-' * 72,
    ]

    rename_count = 0
    skip_count = 0
    unchanged_count = 0
    output_lines = list(header)

    def emit(line=''):
        print(line)
        output_lines.append(line)

    for line in header:
        print(line)

    # Phase 1: collect directories top-down so we can prune excluded subtrees.
    dirs_to_process = []
    for dirpath, dirnames, _files in os.walk(root_dir, topdown=True):
        for d in dirnames[:]:
            if d in exclude_set:
                excluded_path = os.path.join(dirpath, d)
                emit(f'[EXCLUDED] {excluded_path} (contents skipped, dir itself still renamed)')
                dirs_to_process.append(excluded_path)  # rename dir but not its contents
                dirnames.remove(d)
        if os.path.abspath(dirpath) != root_dir:
            dirs_to_process.append(dirpath)

    # Phase 2: process deepest paths first (bottom-up) so parent renames
    # never invalidate a child path we haven't handled yet.
    dirs_to_process.sort(key=lambda p: p.count(os.sep), reverse=True)

    for dirpath in dirs_to_process:
        basename = os.path.basename(dirpath)
        parent = os.path.dirname(dirpath)

        new_name, skip_reason = compute_new_name(basename)

        if skip_reason:
            emit(f'[SKIP]    {dirpath}')
            emit(f'          reason: {skip_reason}')
            skip_count += 1
            continue

        if new_name is None:
            unchanged_count += 1
            continue

        new_path = os.path.join(parent, new_name)

        if os.path.exists(new_path):
            emit(f'[SKIP]    {dirpath}')
            emit(f'          reason: target already exists → {new_name!r}')
            skip_count += 1
            continue

        tag = '[DRY RUN]' if dry_run else '[RENAMED]'
        emit(f'{tag} {dirpath}')
        emit(f'          → {new_path}')

        if not dry_run:
            os.rename(dirpath, new_path)

        rename_count += 1

    summary = (
        f'\nDone.  '
        f'Renames: {rename_count}  '
        f'Skips: {skip_count}  '
        f'Unchanged: {unchanged_count}'
    )
    emit(summary)

    with open(log_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines) + '\n')

    print(f'Log written to: {log_path}')


if __name__ == '__main__':
    main()
