"""
session_order_utils.py

Shared chronological-ordering check for track2p session lists. track2p's
registration/chaining only ever compares LIST-adjacent sessions (index i to
i+1), never by calendar date -- and the track2p GUI does not sort sessions
by date, it just preserves whatever order you clicked "->" to move them
into the paths list (see track2p/gui/t2p_wd.py). Adding a new batch of
sessions later silently appends them out of chronological order unless
something catches it.

Import ensure_chronological_order() into any launcher script that's about
to run actual registration (run_t2p / run_t2p_gap_tolerant) right after
track_ops.all_ds_path is set, so a misordered list gets caught (and fixed)
automatically instead of silently corrupting an expensive elastix run.

Assumes session folder names start with a date in MM-DD-YY format (e.g.
'01-06-26-000'). Two-digit years are interpreted as 2000+YY.
"""

import os
import re
from datetime import date
import numpy as np

DATE_RE = re.compile(r'^(\d{2})-(\d{2})-(\d{2})')


def parse_session_date(path):
    name = os.path.basename(os.path.normpath(path))
    m = DATE_RE.match(name)
    if not m:
        raise ValueError(f"Couldn't parse a MM-DD-YY date from session folder name: '{name}'")
    mm, dd, yy = (int(x) for x in m.groups())
    return date(2000 + yy, mm, dd)


def find_session_dirs(*parent_dirs, plane=0, sanity_check=True, name_pattern=None,
                       exclude_pattern=None, exclude_earliest_date=True):
    """
    Scan one or more parent directories for immediate subdirectories whose
    names start with a recognizable MM-DD-YY session date, and return their
    full paths. Anything that doesn't parse as a dated session folder
    (stray files, differently-named folders, etc.) is silently skipped.

    Order of the returned list is whatever os.listdir/parent-argument order
    happens to give -- doesn't matter, feed it straight into
    ensure_chronological_order() (or just use it as ALL_DS_PATH directly;
    run_gap_tolerant.py / run_exclude_session.py already sort it for you).

    Handy for filling in ALL_DS_PATH without typing out each path by hand.
    Pass multiple parent directories if your sessions are split across more
    than one location (e.g. an older archive folder plus a newer one):

        ALL_DS_PATH = find_session_dirs('/data/2025_recordings', '/data/2026_recordings')

    MAPPING-DAY FILTERING (exclude_earliest_date, default True): if your raw
    data parent also contains initial mapping sessions before the
    longitudinal series starts, this drops every session sharing the
    chronologically EARLIEST date found, regardless of suffix -- matching a
    recording convention where day 1 is exclusively mapping (often several
    runs, suffixed 000/001/002/...) and the longitudinal series only starts
    the next session. This is deliberately date-based rather than
    suffix-based, since the mapping day's runs can include a '000' suffix
    that's otherwise indistinguishable by name from a real longitudinal
    session. Set to False if your data doesn't follow this convention.

    FOV-SERIES FILTERING (name_pattern / exclude_pattern): if a subject has
    more than one FOV recorded longitudinally (rare, but possible -- e.g. a
    second FOV's series using a '-001' suffix instead of '-000'), use
    name_pattern (folder name must fully match, via re.fullmatch) to select
    just the series you want, or exclude_pattern (folder name must NOT
    contain, via re.search) to drop something specific:

        ALL_DS_PATH = find_session_dirs('/data/raw', name_pattern=r'\\d{2}-\\d{2}-\\d{2}-000')
        ALL_DS_PATH = find_session_dirs('/data/raw', exclude_pattern=r'mapping')

    Combine both when useful -- name_pattern/exclude_pattern are applied
    first, then exclude_earliest_date drops the earliest date remaining
    AFTER that filtering (so selecting name_pattern=r'...-001' for a second
    FOV still correctly drops that FOV's own mapping-day '-001' run, if one
    exists, rather than only ever matching the default '-000' mapping runs).

    If date+suffix ever leaves genuine ambiguity, the unambiguous ground
    truth is the stimulus protocol itself (mapping: WN + tones at 2
    intensities, 1/octave; longitudinal: WN + tones at 3 intensities,
    4/octave) -- not something this function can check, since that lives in
    stimulus metadata outside of what track2p touches, but worth a manual
    look if the printed list here doesn't match expectations.

    By default (sanity_check=True) also prints each found session's
    detected-ROI count and flags two common mistakes BEFORE you spend any
    registration compute on them:
      - pointing this at a "matched_suite2p" folder -- that's track2p's own
        regenerated output from a PRIOR run (see save_in_s2p_format()),
        which mirrors real session folder NAMES exactly, so it matches the
        date pattern here too even though it's not raw data
      - sessions with suspiciously low/zero ROI counts relative to the group
    Pass sanity_check=False to skip this (e.g. for very large session sets
    where the extra stat.npy loads matter).
    """
    found = []
    for parent in parent_dirs:
        for name in sorted(os.listdir(parent)):
            full = os.path.join(parent, name)
            if not os.path.isdir(full):
                continue
            try:
                parse_session_date(full)
            except ValueError:
                continue  # not a dated session folder -- skip silently
            if name_pattern is not None and not re.fullmatch(name_pattern, name):
                continue
            if exclude_pattern is not None and re.search(exclude_pattern, name):
                continue
            found.append(full)

    if exclude_earliest_date and found:
        dated = [(parse_session_date(p), p) for p in found]
        earliest = min(d for d, _ in dated)
        dropped = [p for d, p in dated if d == earliest]
        found = [p for d, p in dated if d != earliest]
        print(f'[find_session_dirs] excluding {len(dropped)} session(s) on {earliest.isoformat()} '
              f'(earliest date found -- assumed to be the mapping-only day):')
        for p in dropped:
            print(f'    {os.path.basename(p)}')
        print()

    if sanity_check:
        _sanity_check_session_dirs(found, plane=plane)

    return found


def _sanity_check_session_dirs(found, plane=0):
    for full in found:
        parts = os.path.normpath(full).split(os.sep)
        if 'matched_suite2p' in parts:
            print(f'  [find_session_dirs] WARNING: {full}')
            print(f'      is inside a "matched_suite2p" folder -- that\'s track2p\'s own '
                  f'regenerated output from a PRIOR run, not raw suite2p data. You probably '
                  f'want the original data directory instead.')

    print(f'\n[find_session_dirs] found {len(found)} session(s) -- ROI counts (plane {plane}):')
    counts = []
    for full in found:
        stat_path = os.path.join(full, 'suite2p', f'plane{plane}', 'stat.npy')
        if not os.path.exists(stat_path):
            print(f'  {os.path.basename(full)}: NO suite2p/plane{plane}/stat.npy found!')
            counts.append(0)
            continue
        n = len(np.load(stat_path, allow_pickle=True))
        counts.append(n)
        print(f'  {os.path.basename(full)}: {n} ROIs')

    if counts:
        med = float(np.median(counts))
        if max(counts) == 0 or (med > 0 and min(counts) < 0.05 * med):
            print('\n  [find_session_dirs] WARNING: ROI counts look suspicious (zero, or wildly '
                  'inconsistent with the group) -- double-check these are the right directories '
                  'before running anything expensive.')
    print()


def ensure_chronological_order(all_ds_path, verbose=True):
    """
    Returns a chronologically-sorted copy of all_ds_path. If the input was
    already sorted, returns it unchanged (as a copy) with no fuss. If it
    needed reordering, prints a loud, hard-to-miss warning with the
    before/after order -- it never fixes something silently.
    """
    dated = [(parse_session_date(p), p) for p in all_ds_path]
    dated_sorted = sorted(dated, key=lambda x: x[0])
    sorted_paths = [p for _, p in dated_sorted]

    if sorted_paths == list(all_ds_path):
        if verbose:
            print('[session order] all_ds_path is already chronological -- OK.')
        return sorted_paths

    print('\n' + '!' * 70)
    print('!! all_ds_path was NOT in chronological order -- auto-correcting !!')
    print('!' * 70)
    print('This almost always means sessions were added to the track2p GUI in a batch')
    print('that got appended after an earlier batch without re-sorting by date.')
    print('\nOriginal (as loaded):')
    for i, p in enumerate(all_ds_path):
        print(f'  [{i}] {os.path.basename(p)}')
    print('\nCorrected (chronological):')
    for i, p in enumerate(sorted_paths):
        print(f'  [{i}] {os.path.basename(p)}')
    print('!' * 70 + '\n')

    return sorted_paths


def load_all_ds_path(save_path):
    """
    Load just the all_ds_path session list from a PREVIOUS track2p run's
    track_ops.npy -- for chaining runs together (e.g. round 2 of
    run_exclude_session.py dropping a second session from round 1's
    already-once-excluded list) while still using TRACK_OPS_CFG for
    settings, instead of falling back to SETTINGS_SOURCE_PATH.

    Do NOT use find_session_dirs() for this -- that scans a RAW DATA
    directory for dated session subfolders, and a track2p OUTPUT directory
    doesn't have any (it holds track_ops.npy, match_mat.npy, plots, etc.
    directly), so pointing find_session_dirs() at a previous run's
    save_path silently returns an empty list instead of what you meant.

    save_path is the run's actual output directory -- the one containing
    track_ops.npy directly (usually <NEW_BASE_PATH>/track2p, since
    run_t2p()/run_t2p_gap_tolerant() append that 'track2p' subfolder
    themselves onto whatever save_path you originally gave them).

        ALL_DS_PATH = load_all_ds_path('/path/to/prev_run/track2p')
    """
    ops_path = os.path.join(save_path, 'track_ops.npy')
    if not os.path.exists(ops_path):
        raise FileNotFoundError(
            f"No track_ops.npy at {ops_path} -- save_path should be the run's actual output "
            f"directory (often <NEW_BASE_PATH>/track2p), not NEW_BASE_PATH itself."
        )
    track_ops_dict = np.load(ops_path, allow_pickle=True).item()
    return list(track_ops_dict['all_ds_path'])
