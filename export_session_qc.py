"""
export_session_qc.py

Exports just enough from your suite2p sessions for a MATLAB-side visual QC
check, since ops.npy is a pickled Python dict (saved with allow_pickle=True)
and MATLAB's usual .npy readers can't parse pickled objects -- only plain
numeric numpy arrays. This pulls out the numeric pieces (mean images,
iscell-filtered cell counts) and writes a single .mat file that MATLAB
loads natively with load().

Run from your track2p conda env (needs numpy + scipy only):

    python export_session_qc.py /path/to/existing/track2p/save_path
    python export_session_qc.py /path/to/existing/track2p/save_path --sessions 6 7 8

Reuses track_ops.npy from that save_path (all_ds_path, iscell_thr, etc.) --
same config as your actual run, no need to re-type session paths. By
default exports mean images for EVERY session found in track_ops.npy --
pass --sessions to restrict to specific 0-indexed sessions instead (e.g.
for a quick look at just the ones you already suspect).

Output: session_qc.mat, saved into save_path itself (i.e. right alongside
track_ops.npy / plane{j}_match_mat.npy) unless --out overrides it. Contains:
  meanImg         -- 1xK cell array, one meanImg per exported session (plane --plane)
  session_labels  -- 1xK cell array of session folder names, matching meanImg order
  iscell_counts   -- [n_sessions] vector, detected-cell count per session (plane --plane)
  all_labels      -- cell array of ALL session folder names, matching iscell_counts order
"""

import os
import argparse
import numpy as np
import scipy.io as sio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='existing track2p save_path (contains track_ops.npy)')
    parser.add_argument('--sessions', type=int, nargs='+', default=None,
                         help='0-indexed session indices to export mean images for '
                              '(default: all sessions found in track_ops.npy)')
    parser.add_argument('--plane', type=int, default=0, help='plane index (default 0)')
    parser.add_argument('--out', default=None,
                         help='output .mat path (default: session_qc.mat inside save_path)')
    args = parser.parse_args()

    out_path = args.out if args.out is not None else os.path.join(args.save_path, 'session_qc.mat')

    track_ops = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    all_ds_path = track_ops['all_ds_path']
    iscell_thr = track_ops.get('iscell_thr', 0.5)
    plane = args.plane

    print(f'{len(all_ds_path)} sessions found in track_ops.npy (iscell_thr={iscell_thr})')

    sessions = args.sessions if args.sessions is not None else list(range(len(all_ds_path)))

    # mean images for the requested sessions
    mean_imgs = []
    mean_labels = []
    for s in sessions:
        ops_path = os.path.join(all_ds_path[s], 'suite2p', f'plane{plane}', 'ops.npy')
        ops = np.load(ops_path, allow_pickle=True).item()
        mean_imgs.append(ops['meanImg'].astype(np.float64))
        mean_labels.append(os.path.basename(all_ds_path[s]))
        print(f'  session {s} ({mean_labels[-1]}): meanImg {ops["meanImg"].shape}')

    # iscell-filtered cell counts across ALL sessions (same convention track2p itself uses)
    counts = []
    all_labels = []
    for ds_path in all_ds_path:
        iscell_path = os.path.join(ds_path, 'suite2p', f'plane{plane}', 'iscell.npy')
        iscell = np.load(iscell_path, allow_pickle=True)
        if iscell_thr is None:
            n = int(np.sum(iscell[:, 0] == 1))
        else:
            n = int(np.sum(iscell[:, 1] > iscell_thr))
        counts.append(n)
        all_labels.append(os.path.basename(ds_path))
        print(f'  {os.path.basename(ds_path)}: {n} cells')

    # build as an explicit (N,) object array via item-by-item assignment --
    # np.array(mean_imgs, dtype=object) would silently STACK these into a
    # plain 3D array instead of a cell array whenever all the mean images
    # happen to share the same shape (as they normally will here), which
    # would break MATLAB's data.meanImg{i} cell indexing below
    mean_imgs_cell = np.empty(len(mean_imgs), dtype=object)
    for i, img in enumerate(mean_imgs):
        mean_imgs_cell[i] = img

    sio.savemat(out_path, {
        'meanImg': mean_imgs_cell,
        'session_labels': np.array(mean_labels, dtype=object),
        'iscell_counts': np.array(counts),
        'all_labels': np.array(all_labels, dtype=object),
    })
    print(f'\nSaved {os.path.abspath(out_path)} -- load this in MATLAB with compare_session_qc.m')


if __name__ == '__main__':
    main()
