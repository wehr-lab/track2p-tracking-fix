"""
run_gap_tolerant.py

Standalone launcher for fix1_gap_tolerant_chain.run_t2p_gap_tolerant(), since
track2p normally only gets run through its PyQt GUI (`python -m track2p` ->
track2p/gui/t2p_wd.py's Track2pWindow.run(), which builds track_ops from GUI
fields and calls track2p.t2p.run_t2p() directly -- there's no run script to
adapt).

Per-run settings (TRACK_OPS_CFG, ALL_DS_PATH, NEW_BASE_PATH, MAX_GAP,
N_WORKERS, ...) live in run_gap_tolerant_settings.py, NOT in this file --
edit that file before each run. This script is pure logic: Claude revises
it freely without ever touching your settings.

TIP -- also usable as a cheap "vanilla" runner: set MAX_GAP = 1 in the
settings file and this produces output IDENTICAL to plain
track2p.t2p.run_t2p(), at the same cost. With max_gap=1,
get_all_pl_match_mat_gap's only gap value is 1, so get_assign(i, i+1, ...)
always takes the k==i+1 fast path (the already-computed consecutive-pair
assignment) and never triggers an extra elastix call -- so 0 gap
registrations happen, and the forward-only, permanent-truncation-on-failure
chaining logic collapses to exactly vanilla's. Handy for screening (steps
0/1 of track2p_fix_workflow.md) when you want
find_session_dirs()/TRACK_OPS_CFG/checkpointing/auto-ordering but don't
want to pay for real gap-tolerant chaining yet.

TIP -- N_WORKERS (in the settings file) lets gap registrations run in
parallel across worker processes (Python's closest equivalent to MATLAB's
parfor) instead of one at a time. Leave it at 1 for the original
lazy/sequential behavior; see precompute_gap_pairs_parallel()'s docstring
in fix1_gap_tolerant_chain.py for the trade-off and two real gotchas before
raising it. This script's logic below is wrapped in
`if __name__ == '__main__':` specifically so that's safe to use.

Usage:

    python run_gap_tolerant.py

Requires fix1_gap_tolerant_chain.py, session_order_utils.py,
track_ops_config.py, and run_gap_tolerant_settings.py to be importable --
put them in the same folder as this script (simplest), or anywhere on your
PYTHONPATH.

Automatically checks/fixes chronological session order before running (see
session_order_utils.py) -- the track2p GUI does not sort sessions by date,
so a list built by adding sessions to the GUI in more than one batch can
silently end up out of order, which corrupts registration since track2p
only ever compares list-adjacent sessions.

IMPORTANT -- which track2p gets imported depends on where you run this from:
`import track2p` resolves to whatever's first on sys.path, and if you run
this script from a plain working directory (not the repo root), Python
falls back to whatever's pip/conda-installed in site-packages -- which, in
this env, is a stale copy missing save_match_diagnostics()/npy_to_s2p()
(the git clone at GIT_CLONE_PATH below has the current, patched version
that actually produced match_diagnostics.npy). The sys.path.insert below
forces the git clone to win regardless of cwd, so this runs the same
track2p code whether you launch it from here or from the repo itself.
"""

import sys
GIT_CLONE_PATH = '/Users/wehr/git/track2p'   # confirm with: python -c "import track2p; print(track2p.__file__)"
sys.path.insert(0, GIT_CLONE_PATH)

import numpy as np
from track2p.ops.default import DefaultTrackOps
from fix1_gap_tolerant_chain import run_t2p_gap_tolerant
from session_order_utils import ensure_chronological_order
from track_ops_config import load_track_ops
from run_gap_tolerant_settings import (
    TRACK_OPS_CFG, SETTINGS_SOURCE_PATH, ALL_DS_PATH, NEW_BASE_PATH, MAX_GAP, N_WORKERS,
)


def main():
    if TRACK_OPS_CFG is not None:
        track_ops = load_track_ops(TRACK_OPS_CFG)
        if ALL_DS_PATH is None:
            raise ValueError('ALL_DS_PATH must be set explicitly in run_gap_tolerant_settings.py '
                              'when using TRACK_OPS_CFG (a .cfg file has no session list to fall back to)')
        track_ops.all_ds_path = ALL_DS_PATH
    elif SETTINGS_SOURCE_PATH is not None:
        track_ops = DefaultTrackOps()
        track_ops_dict = np.load(f'{SETTINGS_SOURCE_PATH}/track_ops.npy', allow_pickle=True).item()
        track_ops.from_dict(track_ops_dict)   # restores reg_chan, transform_type, thr_method,
                                               # iscell_thr, matching_method, etc. -- and all_ds_path,
                                               # which gets overridden below if ALL_DS_PATH is set
        if ALL_DS_PATH is not None:
            track_ops.all_ds_path = ALL_DS_PATH
    else:
        raise ValueError('Set either TRACK_OPS_CFG or SETTINGS_SOURCE_PATH in run_gap_tolerant_settings.py')

    track_ops.all_ds_path = ensure_chronological_order(track_ops.all_ds_path)  # catches/fixes GUI mis-ordering

    track_ops.save_path = NEW_BASE_PATH   # override so this run doesn't collide with the old one

    run_t2p_gap_tolerant(track_ops, max_gap=MAX_GAP, n_workers=N_WORKERS)


# Everything above this guard is safe for a worker process to re-import
# without side effects -- required for N_WORKERS > 1. macOS's default
# 'spawn' start method makes each ProcessPoolExecutor worker re-import this
# script to locate the work function; without this guard, every worker
# would re-run main() itself (including spawning its own worker pool,
# recursively) as a side effect of just being imported.
if __name__ == '__main__':
    main()
