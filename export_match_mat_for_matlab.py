"""
export_match_mat_for_matlab.py

Converts a track2p plane{j}_match_mat.npy into a .mat file MATLAB can load
natively, translated into a form directly usable as a row index into each
session's RAW suite2p output (F.npy/Fneu.npy/stat.npy) -- not track2p's
own matched_suite2p output (see WHY NOT matched_suite2p_dir below).

WHY THE RAW VALUES NEED TRANSLATION: track2p's match_mat entries are NOT
raw stat.npy/F.npy row indices. They're indices into each session's
ISCELL-FILTERED ROI list -- confirmed directly from track2p's own source
(match/utils.py's get_cost_mat(): "these are the indices of the ROIs after
iscell", and t2p.py's generate_suite2p_indices(), which does the exact
same translation this script now does before ever handing indices to
downstream code). Using a match_mat value directly as a raw F.npy row
index is silently wrong -- it looks like a plausible row number, just the
wrong one, until it eventually runs out of bounds (which is exactly the
"match_mat(59,1) = 59 exceeds session ...'s suite2p ROI count (58)" error
this script was written to fix).

WHY NOT matched_suite2p_dir: track2p's own save_in_s2p_format() -- called
unmodified by run_gap_tolerant.py -- unconditionally filters down to
STRICT-AND rows before writing that folder at all:
    t2p_match_mat_allday = t2p_match_mat[~np.any(t2p_match_mat == None, axis=1), :]
So matched_suite2p_dir never contains partial-track cells, regardless of
what gap-tolerant chaining (fix #1) or partial-track reporting (fix #3)
found upstream -- pointing BuildResponseTensor.m at it defeats those fixes
entirely, not just for cells with gaps but for the whole missing-data
convention this file is part of. The fix is to read each session's F.npy
straight from its RAW suite2p output (track_ops.all_ds_path), which is
what this script now exports the path list for.

WHAT THIS PRODUCES: plane{j}_match_mat.mat, containing:
  match_mat     -- double, [nCells x nSessions]. NaN = no match in that
                   session; otherwise the 1-indexed row of that session's
                   RAW (track_ops.all_ds_path) F.npy/Fneu.npy/stat.npy.
  session_paths -- cell array of nSessions strings, track_ops.all_ds_path
                   in the same column order as match_mat. This is the
                   authoritative session list/order for this run --
                   BuildResponseTensor.m should read F.npy from THESE
                   paths, not by scanning matched_suite2p_dir.

Does NOT touch the original plane{j}_match_mat.npy -- track2p's own
Python-side code (plotting, save_in_s2p_format, etc.) still needs the
original object-dtype version with real None entries and iscell-filtered
index values.

Usage:
    python export_match_mat_for_matlab.py /path/to/track2p_save_path --plane 0

    # Same run's data, but this computer mounts it somewhere else than the
    # computer track2p originally ran on (e.g. track2p ran against
    # /scratch/wehrlab/2P5XFAD/... on the acquisition machine; here that
    # mouse's data is only reachable via a NAS mount):
    python export_match_mat_for_matlab.py /path/to/track2p_save_path --plane 0 \
        --path-remap /scratch/wehrlab/2P5XFAD=/Volumes/wehrlab/2P5XFAD

Looks for <save_path>/plane{j}_match_mat.npy and <save_path>/track_ops.npy
(for all_ds_path and iscell_thr), writes <save_path>/plane{j}_match_mat.mat
alongside them by default.

PATH REMAPPING (--path-remap): track_ops.npy's all_ds_path is whatever
absolute paths were in scope on the computer that ran run_gap_tolerant.py
-- baked in at track2p run time, not re-derived here. Running this script
on a DIFFERENT computer (e.g. BuildResponseTensor.m calling it via
cfg.pythonBin/system() from your laptop, when track2p itself ran on a
rig/analysis machine with different mount points for the same underlying
data -- 2026-08-13, wehr5659) means those baked-in paths may not exist
locally even though the same session data is reachable via a different
mount (a NAS path here, vs. local /scratch there). --path-remap OLD=NEW
(repeatable) rewrites any all_ds_path entry starting with OLD to start
with NEW instead, BEFORE either reading iscell.npy/F.npy for the
match_mat translation below or writing session_paths into the output
.mat -- so BuildResponseTensor.m (which reads F.npy/Fneu.npy straight
from session_paths) gets already-correct paths for this computer without
needing its own separate remap logic. See DriftConfig.m's cfg.pathRemap.
"""

import os
import argparse
import numpy as np
from scipy.io import savemat


def remap_path(path, remaps):
    """remaps: list of (old_prefix, new_prefix) tuples, checked in order --
    first prefix match wins. Returns `path` unchanged if none match."""
    for old_prefix, new_prefix in remaps:
        if path.startswith(old_prefix):
            return new_prefix + path[len(old_prefix):]
    return path


def get_iscell_valid_indices(iscell, iscell_thr):
    """Same selection track2p itself uses (match/utils.py's get_cost_mat
    callers, t2p.py's save_in_s2p_format/generate_suite2p_indices) --
    must match exactly, or the translation below will be wrong."""
    if iscell_thr is None:
        return np.where(iscell[:, 0] == 1)[0]
    return np.where(iscell[:, 1] > iscell_thr)[0]


def convert_match_mat(match_mat, valid_idx_per_session):
    """match_mat: object-dtype array, entries None or an iscell-filtered
    index (int-like). valid_idx_per_session[s] is that session's
    get_iscell_valid_indices() result. Returns a float64 array:
    None -> NaN, iscell-filtered index -> RAW row + 1 (0-indexed Python ->
    1-indexed MATLAB)."""
    n_rows, n_cols = match_mat.shape
    out = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    for i in range(n_rows):
        for j in range(n_cols):
            val = match_mat[i, j]
            if val is None:
                continue
            valid_idx = valid_idx_per_session[j]
            raw_row = valid_idx[int(val)]
            out[i, j] = float(raw_row) + 1.0
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing plane{j}_match_mat.npy + track_ops.npy')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--out', default=None,
                         help='output path (default: <save_path>/plane{j}_match_mat.mat)')
    parser.add_argument('--path-remap', action='append', default=[],
                         metavar='OLD_PREFIX=NEW_PREFIX',
                         help='Rewrite all_ds_path entries starting with OLD_PREFIX to start with '
                              'NEW_PREFIX instead (repeatable). Use when this session\'s track_ops.npy '
                              'was written on a different computer than this one, with a different '
                              'mount point for the same underlying data -- see the module docstring.')
    args = parser.parse_args()

    remaps = []
    for spec in args.path_remap:
        if '=' not in spec:
            raise SystemExit(
                f'--path-remap expects OLD_PREFIX=NEW_PREFIX, got {spec!r} (no \'=\' found).')
        old_prefix, new_prefix = spec.split('=', 1)
        remaps.append((old_prefix, new_prefix))

    in_path = os.path.join(args.save_path, f'plane{args.plane}_match_mat.npy')
    match_mat = np.load(in_path, allow_pickle=True)

    track_ops_path = os.path.join(args.save_path, 'track_ops.npy')
    track_ops = np.load(track_ops_path, allow_pickle=True).item()
    all_ds_path = list(track_ops['all_ds_path'])
    iscell_thr = track_ops.get('iscell_thr', None)

    if remaps:
        remapped = [remap_path(p, remaps) for p in all_ds_path]
        n_changed = sum(1 for old, new in zip(all_ds_path, remapped) if old != new)
        print(f'--path-remap: rewrote {n_changed}/{len(all_ds_path)} session path(s):')
        for old, new in zip(all_ds_path, remapped):
            if old != new:
                print(f'  {old}\n  -> {new}')
        if n_changed == 0:
            print('  (no all_ds_path entry matched any given OLD_PREFIX -- check for a typo.)')
        all_ds_path = remapped

    n_cells, n_sessions = match_mat.shape
    if len(all_ds_path) != n_sessions:
        raise SystemExit(
            f'match_mat has {n_sessions} session column(s) but track_ops.npy\'s all_ds_path has '
            f'{len(all_ds_path)} session(s) -- these must be from the same run. Nothing written.'
        )

    valid_idx_per_session = []
    for s, ds_path in enumerate(all_ds_path):
        iscell_path = os.path.join(ds_path, 'suite2p', f'plane{args.plane}', 'iscell.npy')
        iscell = np.load(iscell_path, allow_pickle=True)
        valid_idx = get_iscell_valid_indices(iscell, iscell_thr)
        valid_idx_per_session.append(valid_idx)

    n_present_before = int(np.sum(match_mat != None))  # noqa: E711
    out = convert_match_mat(match_mat, valid_idx_per_session)
    n_present_after = int(np.sum(~np.isnan(out)))
    assert n_present_before == n_present_after, (
        f'Sanity check failed: {n_present_before} non-None entries in the original vs. '
        f'{n_present_after} non-NaN entries after conversion -- conversion logic is wrong, '
        f'nothing was written.'
    )

    # Bounds check against each session's ACTUAL raw F.npy row count -- catch an
    # out-of-sync match_mat/suite2p output here, at export time, instead of deep
    # inside a 7-step MATLAB pipeline.
    for s, ds_path in enumerate(all_ds_path):
        f_path = os.path.join(ds_path, 'suite2p', f'plane{args.plane}', 'F.npy')
        n_raw = np.load(f_path, mmap_mode='r').shape[0]
        col = out[:, s]
        col_present = col[~np.isnan(col)]
        if col_present.size and col_present.max() > n_raw:
            raise SystemExit(
                f'Session {s} ({ds_path}): translated match_mat row {int(col_present.max())} exceeds '
                f'this session\'s raw F.npy row count ({n_raw}) -- match_mat, iscell.npy, and F.npy are '
                f'out of sync for this session. Nothing written.'
            )

    out_path = args.out if args.out is not None else os.path.join(
        args.save_path, f'plane{args.plane}_match_mat.mat')
    savemat(out_path, {
        'match_mat': out,
        'session_paths': np.array(all_ds_path, dtype=object),
    })

    print(f'{in_path}')
    print(f'  {n_cells} candidate cell(s) x {n_sessions} session(s)')
    print(f'  {n_present_after} present cell-session entries '
          f'({100 * n_present_after / (n_cells * n_sessions):.1f}%)')
    print(f'Wrote {out_path}')
    print('  match_mat: double, NaN = no match, else 1-indexed RAW suite2p row (translated through iscell)')
    print('  session_paths: track_ops.all_ds_path, same column order as match_mat')


if __name__ == '__main__':
    main()
