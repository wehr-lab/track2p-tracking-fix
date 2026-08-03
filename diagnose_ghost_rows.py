"""
diagnose_ghost_rows.py

Run this LOCALLY against an existing track2p output folder (the
plane{j}_match_mat.npy is small; no need to upload the large suite2p/NAS
data to Claude).

MOTIVATION (see Drift repo's SESSION_LOG.md, 2026-07-29): 558/1078
(51.8%) candidate cells for one mouse turned out to be "ghost" rows --
NaN/None in EVERY session, no real match anywhere. Confirmed (via
export_match_mat_for_matlab.py's own n_present_before==n_present_after
assertion) that these rows are already all-None in plane{j}_match_mat.npy
itself, before MATLAB/BuildResponseTensor.m ever sees them -- so the
root cause is somewhere in this repo's chain construction (or upstream
in the external track2p package), not in the Drift repo.

Reading add_anchor_agnostic_chains() in fix1_gap_tolerant_chain.py (the
"fix #2" feature, on by default per run_gap_tolerant_settings.py) looks,
on inspection, like it should be IMPOSSIBLE to produce an all-None row --
every kept chain includes its own anchor session, and chains shorter
than min_chain_length (default 2) are discarded before being written.
This script tests that directly: if the all-None rows are a CONTIGUOUS
block at the END of match_mat's row order, that's strong evidence they
came from add_anchor_agnostic_chains's np.vstack(...) append (fix #1's
init_all_pl_match_mat rows come first, fix #2's new_rows are appended
after) -- meaning the bug (if it's not just corruption from something
downstream, e.g. a session-exclusion/reorder step touching this array
afterward) is inside fix #2's row construction specifically, contradicting
what the code looks like it should guarantee. If the all-None rows are
scattered throughout instead, that points somewhere else entirely (fix
#1's own init_all_pl_match_mat / the external track2p package, or a
downstream reordering step).

Usage:
    python diagnose_ghost_rows.py /path/to/track2p_save_path --plane 0

Prints a summary; does not modify anything.
"""

import sys
import os
import argparse
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path')
    parser.add_argument('--plane', type=int, default=0)
    args = parser.parse_args()

    match_mat_path = os.path.join(args.save_path, f'plane{args.plane}_match_mat.npy')

    if not os.path.exists(match_mat_path):
        sys.exit(f'Not found: {match_mat_path}')

    match_mat = np.load(match_mat_path, allow_pickle=True)

    n_cells, n_sessions = match_mat.shape

    print(f'Loaded {match_mat_path}')
    print(f'  {n_cells} candidate cell(s) x {n_sessions} session(s)')

    is_none = np.array([[v is None for v in row] for row in match_mat])

    ghost = is_none.all(axis=1)

    n_ghost = int(ghost.sum())

    print(f'\nGhost rows (None in EVERY session): {n_ghost}/{n_cells} '
          f'({100 * n_ghost / n_cells:.1f}%)')

    if n_ghost == 0:
        print('No ghost rows found -- nothing further to diagnose.')
        return

    ghost_idx = np.where(ghost)[0]

    # Contiguity check: are the ghost rows a single contiguous block, and
    # specifically at the END of the row order (consistent with an
    # np.vstack(...) append, e.g. add_anchor_agnostic_chains's new_rows)?
    is_contiguous = np.array_equal(
        ghost_idx, np.arange(ghost_idx.min(), ghost_idx.min() + len(ghost_idx))
    )
    is_at_tail = ghost_idx.min() == n_cells - n_ghost

    print(f'  First ghost row index : {ghost_idx.min()} (0-indexed)')
    print(f'  Last ghost row index  : {ghost_idx.max()}')
    print(f'  Contiguous block?     : {is_contiguous}')
    print(f'  Sitting at the tail?  : {is_at_tail}  '
          f'(True means rows {n_cells - n_ghost}..{n_cells - 1} are ALL ghosts, '
          'nothing before them)')

    print()
    if is_contiguous and is_at_tail:
        print('CONCLUSION: ghost rows are a contiguous block at the END of '
              'match_mat -- consistent with being appended by an '
              'np.vstack(...) call (e.g. add_anchor_agnostic_chains''s '
              'new_rows in fix1_gap_tolerant_chain.py). Worth checking that '
              'function''s row-construction logic directly (or whether '
              'ANCHOR_AGNOSTIC_SEEDING was even on for this run -- check '
              'run_gap_tolerant_settings.py / this run''s console log).')
    elif is_contiguous:
        print('CONCLUSION: ghost rows form a contiguous block, but NOT at '
              'the very end -- less clean a signal, but still consistent '
              'with a single append/insert step somewhere. Check whether '
              'this run had more than one processing pass (e.g. a resumed '
              'checkpoint, or run_exclude_session.py touching the array '
              'after tracking).')
    else:
        print('CONCLUSION: ghost rows are SCATTERED throughout match_mat, '
              'not a single contiguous append -- less consistent with '
              'add_anchor_agnostic_chains specifically, more consistent '
              'with something in fix #1''s own init_all_pl_match_mat / the '
              'external track2p package itself, or a row-by-row corruption '
              'from a downstream step. Worth checking whether '
              'ANCHOR_AGNOSTIC_SEEDING was even used for this run at all.')

    # Row lengths for the first few ghost rows (sanity: are they really
    # all-None, or does this np.array construction have a subtlety, e.g.
    # np.nan vs None mismatch for some rows?).
    print(f'\nFirst 5 ghost row indices: {ghost_idx[:5].tolist()}')


if __name__ == '__main__':
    main()
