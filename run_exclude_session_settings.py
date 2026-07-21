"""
run_exclude_session_settings.py

Per-run settings for run_exclude_session.py, kept in a separate file on
purpose: when Claude revises run_exclude_session.py's logic, this file is
never touched. Edit the values below by hand before each run.
"""

from session_order_utils import load_all_ds_path  # noqa: F401  (used below)

# Preferred: central settings file (see track_ops_config.py to generate one).
TRACK_OPS_CFG = '/Users/wehr/Documents/Analysis/track2p-tracking-fix/track2p_settings.cfg'

# Legacy alternative: leave TRACK_OPS_CFG as None above and fill this in
# instead to borrow settings directly from an existing track_ops.npy. Its
# all_ds_path also serves as the default session list to exclude FROM, if
# ALL_DS_PATH below is left as None.
SETTINGS_SOURCE_PATH = None

# The session list to exclude FROM. Required if using TRACK_OPS_CFG (a .cfg
# file has no session list to fall back to). If using SETTINGS_SOURCE_PATH
# instead, leave as None to just reuse whatever all_ds_path was saved there.
#
# Scanning raw data fresh:
#     from session_order_utils import find_session_dirs
#     ALL_DS_PATH = find_session_dirs('/Volumes/Projects/2P5XFAD/JarascopeData/wehr5336')
#
# Chaining onto a PREVIOUS track2p run's already-once-excluded list instead
# (what's used below -- this is round 2, excluding a second session from
# round 1's output):
# ALL_DS_PATH = load_all_ds_path(    '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip/track2p')

# Where to write this run's output. Give this a NEW directory -- run_t2p()
# appends its own 'track2p/' subfolder onto whatever save_path you give it,
# so reusing SETTINGS_SOURCE_PATH's parent would land back on it and
# overwrite your original results.
# NEW_BASE_PATH = '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip2'

# Which session to drop. Matched by substring against the session folder
# name (safer than a hardcoded index, since it fails loudly if the string
# doesn't match exactly one session instead of silently dropping the wrong
# one) -- e.g. the date you identified visually.
# EXCLUDE_MATCH = '02-24-26'

ALL_DS_PATH = load_all_ds_path(
    '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip2/track2p'
)
NEW_BASE_PATH = '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip3'
EXCLUDE_MATCH = '12-09-25'