"""
run_exclude_session.py

Cheap test of a "session X is the bottleneck" hypothesis: reruns VANILLA
track2p (unmodified track2p.t2p.run_t2p -- no gap-tolerant chaining, no
other algorithm changes) on an existing session config with one session
excluded, so you can see how much yield moves from removing just that one
session.

Per-run settings (TRACK_OPS_CFG, ALL_DS_PATH, NEW_BASE_PATH, EXCLUDE_MATCH,
...) live in run_exclude_session_settings.py, NOT in this file -- edit that
file before each run. This script is pure logic: Claude revises it freely
without ever touching your settings.

Usage:

    python run_exclude_session.py

At the end it prints the strict-AND(all sessions) tracked-cell count for
the resulting run.

Requires session_order_utils.py, track_ops_config.py, and
run_exclude_session_settings.py to be importable -- same folder as this
script, or on your PYTHONPATH.

Automatically checks/fixes chronological session order before running (see
session_order_utils.py) -- the track2p GUI does not sort sessions by date,
so a list built by adding sessions to the GUI in more than one batch can
silently end up out of order, which corrupts registration since track2p
only ever compares list-adjacent sessions.
"""

import sys
GIT_CLONE_PATH = '/Users/wehr/git/track2p'   # confirm with: python -c "import track2p; print(track2p.__file__)"
sys.path.insert(0, GIT_CLONE_PATH)

import os
import numpy as np
from track2p.ops.default import DefaultTrackOps
from track2p.t2p import run_t2p
from session_order_utils import ensure_chronological_order
from track_ops_config import load_track_ops
from run_exclude_session_settings import (
    TRACK_OPS_CFG, SETTINGS_SOURCE_PATH, ALL_DS_PATH, NEW_BASE_PATH, EXCLUDE_MATCH,
)


def main():
    if TRACK_OPS_CFG is not None:
        track_ops = load_track_ops(TRACK_OPS_CFG)
        if ALL_DS_PATH is None:
            raise ValueError('ALL_DS_PATH must be set explicitly in run_exclude_session_settings.py '
                              'when using TRACK_OPS_CFG (a .cfg file has no session list to fall back to)')
        track_ops.all_ds_path = ALL_DS_PATH
    elif SETTINGS_SOURCE_PATH is not None:
        track_ops = DefaultTrackOps()
        track_ops_dict = np.load(f'{SETTINGS_SOURCE_PATH}/track_ops.npy', allow_pickle=True).item()
        track_ops.from_dict(track_ops_dict)
        if ALL_DS_PATH is not None:
            track_ops.all_ds_path = ALL_DS_PATH
    else:
        raise ValueError('Set either TRACK_OPS_CFG or SETTINGS_SOURCE_PATH in run_exclude_session_settings.py')

    track_ops.all_ds_path = ensure_chronological_order(track_ops.all_ds_path)  # catches/fixes GUI mis-ordering

    print(f'{len(track_ops.all_ds_path)} sessions in original config:')
    for i, p in enumerate(track_ops.all_ds_path):
        print(f'  [{i}] {p}')

    matches = [p for p in track_ops.all_ds_path if EXCLUDE_MATCH in os.path.basename(p)]
    if len(matches) != 1:
        raise ValueError(
            f"EXCLUDE_MATCH='{EXCLUDE_MATCH}' matched {len(matches)} session(s), expected exactly 1: {matches}\n"
            f"Fix EXCLUDE_MATCH in run_exclude_session_settings.py to uniquely identify the session you want to drop."
        )
    excluded_path = matches[0]
    track_ops.all_ds_path = [p for p in track_ops.all_ds_path if p != excluded_path]

    print(f'\nExcluding: {excluded_path}')
    print(f'{len(track_ops.all_ds_path)} sessions remaining:')
    for i, p in enumerate(track_ops.all_ds_path):
        print(f'  [{i}] {p}')

    track_ops.save_path = NEW_BASE_PATH  # override so this run doesn't collide with the original

    run_t2p(track_ops)

    # --- report strict-AND yield ---
    final_save_path = os.path.join(NEW_BASE_PATH, 'track2p')  # run_t2p's init_save_paths() appends this
    for j in range(track_ops.nplanes):
        mm = np.load(os.path.join(final_save_path, f'plane{j}_match_mat.npy'), allow_pickle=True)
        n_tracked = int(np.sum(np.all(mm != None, axis=1)))  # noqa: E711
        print(f'\nPlane {j}: {n_tracked} cells tracked across all {mm.shape[1]} remaining sessions '
              f'(out of {mm.shape[0]} candidate ROIs from session 0)')


if __name__ == '__main__':
    main()
