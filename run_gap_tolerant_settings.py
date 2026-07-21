"""
run_gap_tolerant_settings.py

Per-run settings for run_gap_tolerant.py, kept in a separate file on
purpose: when Claude revises run_gap_tolerant.py's logic, this file is
never touched. Edit the values below by hand before each run, same as
you did inside the launcher script before -- just without the risk of a
code change overwriting your edits.
"""

from session_order_utils import load_all_ds_path  # noqa: F401  (used below)

# Preferred: central settings file (see track_ops_config.py to generate one).
TRACK_OPS_CFG = '/Users/wehr/Documents/Analysis/track2p-tracking-fix/track2p_settings.cfg'

# Legacy alternative: leave TRACK_OPS_CFG as None above and fill this in
# instead to borrow settings directly from an existing track_ops.npy (any
# prior save_path for this subject -- doesn't need to be a run on the
# session list below, doesn't need to be "fresh," doesn't need to be
# vanilla). Also still useful for its ALL_DS_PATH fallback behavior.
SETTINGS_SOURCE_PATH = None

# The session folders for THIS run. Required if using TRACK_OPS_CFG (a
# .cfg file has no session list to fall back to). If using
# SETTINGS_SOURCE_PATH instead, leave as None to just reuse whatever
# all_ds_path was saved there.
#
# Scanning raw data fresh:
#     from session_order_utils import find_session_dirs
#     ALL_DS_PATH = find_session_dirs('/path/to/2025_data', '/path/to/2026_data')
#
# Chaining onto a PREVIOUS track2p run's session list instead (what's used
# below):
#     ALL_DS_PATH = load_all_ds_path('/path/to/prev_run/track2p')
ALL_DS_PATH = load_all_ds_path(
    '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip3/track2p'
)

# Where to write this run's output. Give this a NEW directory, not
# SETTINGS_SOURCE_PATH or its parent -- run_t2p_gap_tolerant() appends a
# 'track2p/' subfolder onto whatever save_path you give it, so reusing the
# parent of an existing run would land back on it and overwrite results.
NEW_BASE_PATH = '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip3_2'

MAX_GAP = 3

# Parallel gap registration across this many worker processes (Python's
# closest equivalent to MATLAB's parfor). 1 = original lazy/sequential
# behavior -- safest default, keep it here until you've validated a higher
# value on a small session subset. See precompute_gap_pairs_parallel()'s
# docstring in fix1_gap_tolerant_chain.py for the trade-off and gotchas.
N_WORKERS = 6
