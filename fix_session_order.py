"""
fix_session_order.py

The track2p GUI does NOT sort sessions chronologically -- Track2pWindow's
paths_list just preserves whatever order you clicked "->" to move sessions
into it (see track2p/gui/t2p_wd.py: move_file_to_paths_list /
run() -> stored_all_ds_path just iterates the widget in display order).
If you add a new batch of sessions to an existing list, they get appended
to the END regardless of their actual date -- which silently corrupts
track2p's registration, since it only ever compares LIST-adjacent sessions
(index i to index i+1), never by calendar date.

This loads an existing track_ops.npy, re-sorts all_ds_path chronologically
by parsing the date out of each session folder's name, and writes a
corrected copy back (after backing up the original) -- no need to rebuild
anything by hand in the GUI.

Assumes session folder names start with a date in MM-DD-YY format (e.g.
'01-06-26-000', '11-13-25-000', matching what screen_sessions.py already
prints for you). Two-digit years are interpreted as 2000+YY.

Usage:
    python fix_session_order.py /path/to/save_path --dry-run
        # just prints current vs. corrected order, writes nothing -- run
        # this FIRST to sanity-check before committing

    python fix_session_order.py /path/to/save_path
        # writes the corrected order back to track_ops.npy (backs up the
        # original to track_ops.npy.pre_sort_backup first)

If match_diagnostics.npy / plane{j}_match_mat.npy already exist at this
save_path, that means a full run already completed on the MIS-ordered
list -- fixing the order afterward does not fix that run's results, since
the actual registrations were computed against the wrong neighbors. You'll
need to re-run track2p from scratch on the corrected config.

NOTE: for a fresh run, prefer session_order_utils.ensure_chronological_order()
(baked into run_gap_tolerant.py / run_exclude_session.py already) so this
never has to be reached for in the first place. This script exists for the
after-the-fact case: a completed run whose session order turns out to have
been wrong.
"""

import os
import re
import shutil
import argparse
from datetime import date
import numpy as np

DATE_RE = re.compile(r'^(\d{2})-(\d{2})-(\d{2})')


def parse_date(path):
    name = os.path.basename(os.path.normpath(path))
    m = DATE_RE.match(name)
    if not m:
        raise ValueError(f"Couldn't parse a MM-DD-YY date from session folder name: '{name}'")
    mm, dd, yy = (int(x) for x in m.groups())
    return date(2000 + yy, mm, dd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing track_ops.npy')
    parser.add_argument('--dry-run', action='store_true', help="print the corrected order, don't write")
    args = parser.parse_args()

    ops_path = os.path.join(args.save_path, 'track_ops.npy')
    track_ops = np.load(ops_path, allow_pickle=True).item()
    all_ds_path = track_ops['all_ds_path']

    dated = [(parse_date(p), p) for p in all_ds_path]

    print('Current order (as loaded):')
    for i, (d, p) in enumerate(dated):
        print(f'  [{i}] {d.isoformat()}  {os.path.basename(p)}')

    dated_sorted = sorted(dated, key=lambda x: x[0])
    already_sorted = [p for _, p in dated_sorted] == all_ds_path

    print(f'\nAlready in chronological order: {already_sorted}')
    if already_sorted:
        print('Nothing to fix.')
        return

    print('\nCorrected order:')
    for i, (d, p) in enumerate(dated_sorted):
        print(f'  [{i}] {d.isoformat()}  {os.path.basename(p)}')

    diag_exists = os.path.exists(os.path.join(args.save_path, 'match_diagnostics.npy'))
    if diag_exists:
        print('\nWARNING: match_diagnostics.npy already exists at this save_path -- a full run '
              'already completed on the MIS-ordered list. Fixing all_ds_path here does NOT fix '
              'that run\'s registration output; you need to re-run track2p from scratch on the '
              'corrected config.')

    if args.dry_run:
        print('\n(--dry-run: not writing anything)')
        return

    backup_path = ops_path + '.pre_sort_backup'
    shutil.copy2(ops_path, backup_path)
    print(f'\nBacked up original to {backup_path}')

    track_ops['all_ds_path'] = [p for _, p in dated_sorted]
    np.save(ops_path, track_ops, allow_pickle=True)
    print(f'Wrote corrected track_ops.npy to {ops_path}')


if __name__ == '__main__':
    main()
