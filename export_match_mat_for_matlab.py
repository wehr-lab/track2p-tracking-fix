"""
export_match_mat_for_matlab.py

Converts a track2p plane{j}_match_mat.npy into a .mat file MATLAB can load
natively -- no npy-matlab / readNPY.m dependency for this file at all.

WHY NOT readNPY.m: track2p saves plane{j}_match_mat.npy as a numpy
OBJECT-dtype array -- each entry is either a Python int (the matched local
ROI row index in that session's own suite2p output) or None (no match for
that cell in that session). Object dtype is how numpy represents a mix of
None and ints in one array; there's no MATLAB-native equivalent, so
readNPY.m's dtype lookup table (which only covers plain numeric/string
dtypes) fails outright on it -- "Unable to perform assignment with 0
elements on the right-hand side" in readNPYheader.m happens because the
object dtype's header string doesn't match anything in that lookup table;
it never gets far enough to actually attempt loading data. An earlier
version of this script worked around that by writing a second .npy (NaN
sentinel, still via readNPY.m) -- switched to .mat instead since the
consumer here is MATLAB-only anyway, so there's no reason to keep going
through a Python-native format + a third-party reader for it.

WHAT THIS PRODUCES: plane{j}_match_mat.mat, containing one variable,
match_mat -- a plain double array, same [nCells x nSessions] shape.
  - None (no match)          -> NaN
  - matched local ROI index  -> index + 1

The +1 converts Python's 0-indexed convention to MATLAB's 1-indexed
convention, so a value in match_mat can be used AS a MATLAB row index into
F.npy/Fneu.npy/stat.npy (loaded via readNPY()) with no separate off-by-one
translation needed at the call site. NaN + 1 is still NaN, so missing
entries stay unambiguous after the shift.

This is also the concrete artifact behind the "match-table" missing-data
convention discussed for fix #2/#3's output (see track2p_fix_workflow.md /
SESSION_LOG.md 2026-07-24-and-later): BuildResponseTensor.m can load this
file once and use match_mat(cellIdx, s) to find which suite2p row (if any)
corresponds to a given global cell in a given session, instead of assuming
every session's F.npy has identical row count/order.

Does NOT touch the original plane{j}_match_mat.npy -- track2p's own
Python-side code (plotting, save_in_s2p_format, etc.) still needs the
original object-dtype version with real None entries.

Usage:
    python export_match_mat_for_matlab.py /path/to/track2p_save_path --plane 0

Looks for <save_path>/plane{j}_match_mat.npy, writes
<save_path>/plane{j}_match_mat.mat alongside it by default.
"""

import os
import argparse
import numpy as np
from scipy.io import savemat


def convert_match_mat(match_mat):
    """match_mat: object-dtype array, entries None or int-like.
    Returns a float64 array: None -> NaN, int -> int + 1 (0-indexed ->
    MATLAB 1-indexed)."""
    n_rows, n_cols = match_mat.shape
    out = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    for i in range(n_rows):
        for j in range(n_cols):
            val = match_mat[i, j]
            if val is not None:
                out[i, j] = float(val) + 1.0
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing plane{j}_match_mat.npy')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--out', default=None,
                         help='output path (default: <save_path>/plane{j}_match_mat.mat)')
    args = parser.parse_args()

    in_path = os.path.join(args.save_path, f'plane{args.plane}_match_mat.npy')
    match_mat = np.load(in_path, allow_pickle=True)

    n_present_before = int(np.sum(match_mat != None))  # noqa: E711
    out = convert_match_mat(match_mat)
    n_present_after = int(np.sum(~np.isnan(out)))
    assert n_present_before == n_present_after, (
        f'Sanity check failed: {n_present_before} non-None entries in the original vs. '
        f'{n_present_after} non-NaN entries after conversion -- conversion logic is wrong, '
        f'nothing was written.'
    )

    out_path = args.out if args.out is not None else os.path.join(
        args.save_path, f'plane{args.plane}_match_mat.mat')
    savemat(out_path, {'match_mat': out})

    n_cells, n_sessions = match_mat.shape
    print(f'{in_path}')
    print(f'  {n_cells} candidate cell(s) x {n_sessions} session(s)')
    print(f'  {n_present_after} present cell-session entries '
          f'({100 * n_present_after / (n_cells * n_sessions):.1f}%)')
    print(f'Wrote {out_path}  (variable: match_mat, double, NaN = no match, '
          f'else 1-indexed local ROI row)')


if __name__ == '__main__':
    main()
