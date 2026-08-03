"""
diagnose_ghost_rows_iscell.py

Follow-up to diagnose_ghost_rows.py -- run this LOCALLY (needs session 0's
iscell.npy, which is small; no need to upload it).

MOTIVATION: diagnose_ghost_rows.py showed 558 ghost rows (all-None across
every session) reproduce EXACTLY (same 558 indices) across two totally
separate runs of wehr5336 -- including a brand-new output directory with
no prior gap_cache_checkpoint.npy to reuse. That rules out checkpoint
staleness (this file's earlier fix) as the cause of THESE rows -- it's
fully deterministic, not a caching artifact.

NEW HYPOTHESIS: init_all_pl_match_mat (external track2p package, not
visible from this repo's source) may seed one candidate row per RAW
detected ROI in session 0, rather than one row per ISCELL-FILTERED ROI.
If so, a ghost row would simply be a raw ROI that suite2p's iscell
classifier rejected (not a real cell) -- it would never have a valid
translated index anywhere, in any session, including session 0 itself,
because "iscell-filtered index" doesn't exist for it in the first place.
This is NOT corruption in that case -- it's `track2p`/`track2p-tracking-fix`
including non-cell candidates in the row count at all, which downstream
code (that assumes every row could be a real trackable cell -- see
BuildResponseTensor.m's ghost-row filter in the Drift repo) doesn't
expect.

This script tests that directly: for every ghost row index, check whether
treating that index as a RAW (pre-iscell) ROI index into session 0's own
iscell.npy lines up with iscell==0 (not a cell).

Usage:
    python diagnose_ghost_rows_iscell.py /path/to/track2p_save_path --plane 0

Requires track_ops.npy (small, has all_ds_path + iscell_thr) and session
0's <all_ds_path[0]>/suite2p/plane{j}/iscell.npy.
"""

import sys
import os
import argparse
import numpy as np


def get_iscell_valid_indices(iscell, iscell_thr):
    """Same selection as export_match_mat_for_matlab.py -- must match
    track2p's own convention exactly."""
    if iscell_thr is None:
        return np.where(iscell[:, 0] == 1)[0]
    return np.where(iscell[:, 1] > iscell_thr)[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path')
    parser.add_argument('--plane', type=int, default=0)
    args = parser.parse_args()

    match_mat_path = os.path.join(args.save_path, f'plane{args.plane}_match_mat.npy')
    track_ops_path = os.path.join(args.save_path, 'track_ops.npy')

    if not os.path.exists(match_mat_path):
        sys.exit(f'Not found: {match_mat_path}')
    if not os.path.exists(track_ops_path):
        sys.exit(f'Not found: {track_ops_path}')

    match_mat = np.load(match_mat_path, allow_pickle=True)
    track_ops = np.load(track_ops_path, allow_pickle=True).item()

    all_ds_path = list(track_ops['all_ds_path'])
    iscell_thr = track_ops.get('iscell_thr', None)

    session0_path = all_ds_path[0]
    iscell_path = os.path.join(session0_path, 'suite2p', f'plane{args.plane}', 'iscell.npy')

    if not os.path.exists(iscell_path):
        sys.exit(f'Not found: {iscell_path}')

    iscell = np.load(iscell_path, allow_pickle=True)

    n_raw_session0 = iscell.shape[0]
    valid_idx = get_iscell_valid_indices(iscell, iscell_thr)

    print(f'Session 0 ({session0_path}):')
    print(f'  raw detected ROIs        : {n_raw_session0}')
    print(f'  iscell-filtered ROIs     : {len(valid_idx)} '
          f'({100 * len(valid_idx) / n_raw_session0:.1f}%)')

    is_none = np.array([[v is None for v in row] for row in match_mat])
    ghost = is_none.all(axis=1)
    ghost_idx = np.where(ghost)[0]

    print(f'\nTotal candidate rows   : {match_mat.shape[0]}')
    print(f'Ghost rows             : {len(ghost_idx)}')

    # Test A: treat ghost row index directly as a RAW session-0 ROI index.
    in_range = ghost_idx[ghost_idx < n_raw_session0]
    out_of_range = ghost_idx[ghost_idx >= n_raw_session0]

    print(f'\n--- Test A: ghost row index AS a raw session-0 ROI index ---')
    print(f'  ghost indices within raw ROI range (0..{n_raw_session0 - 1}): {len(in_range)}/{len(ghost_idx)}')
    print(f'  ghost indices beyond that range (can''t be a raw index)     : {len(out_of_range)}')

    if len(in_range) > 0:
        iscell_at_ghost = iscell[in_range, 0]
        frac_noncell = np.mean(iscell_at_ghost == 0)
        print(f'  of the in-range ghost indices, fraction classified iscell==0 (not a cell): '
              f'{100 * frac_noncell:.1f}%')
        if frac_noncell > 0.9:
            print('  --> STRONG SUPPORT for the raw-ROI-seeding hypothesis: ghost rows are '
                  'overwhelmingly non-cell ROIs by this indexing.')
        elif frac_noncell < 0.1:
            print('  --> CONTRADICTS the raw-ROI-seeding hypothesis under this indexing -- '
                  'ghost rows are mostly classified AS cells, so this isn''t simply iscell '
                  'rejection under a raw-index convention.')
        else:
            print('  --> Mixed / inconclusive under this indexing convention.')

    # Test B: treat ghost row index as a position WITHIN the iscell-filtered list
    # (i.e., match_mat row i corresponds to valid_idx[i], the i-th real cell).
    print(f'\n--- Test B: ghost row index AS a position in the iscell-filtered list ---')
    in_range_b = ghost_idx[ghost_idx < len(valid_idx)]
    print(f'  ghost indices within iscell-filtered range (0..{len(valid_idx) - 1}): '
          f'{len(in_range_b)}/{len(ghost_idx)}')
    print('  (if most ghost indices fall in this range, rows are candidate-pool-ordered by '
          'iscell-filtered position, not raw ROI order -- in which case Test A doesn''t apply '
          'and the ghost mechanism is something else, e.g. inside the external track2p '
          'chaining logic itself.)')

    print(f'\nFirst 10 ghost indices: {ghost_idx[:10].tolist()}')


if __name__ == '__main__':
    main()
