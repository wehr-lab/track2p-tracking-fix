"""
track_ops_config.py

Central, human-editable .cfg file for the track2p settings that stay the
same across runs for a given rig/protocol (reg_chan, transform_type,
thr_method, iscell_thr, etc.) -- an alternative to SETTINGS_SOURCE_PATH
(borrowing settings from an existing track_ops.npy), which works fine but
requires some prior run to already exist, and isn't inspectable/editable
without loading it in Python (it's a pickle).

Only the fields that actually affect track2p's algorithm behavior are
captured -- see FIELDS below. Everything else on DefaultTrackOps
(all_ds_path, save_path, colors, plus anything computed at runtime like
nplanes/nchannels) is either session-specific or set automatically, so it
doesn't belong in a static settings file.

One-time setup -- generate a .cfg from a known-good existing run:

    python track_ops_config.py --export "/path/to/existing/track2p/track_ops.npy" track2p_settings.cfg

After that, edit track2p_settings.cfg by hand if you ever need to change a
setting, and every launcher script just does:

    from track_ops_config import load_track_ops
    track_ops = load_track_ops('track2p_settings.cfg')
    track_ops.all_ds_path = find_session_dirs(...)   # still session-specific, set separately
    track_ops.save_path = ...

Sanity-check what's in an existing .cfg any time with:

    python track_ops_config.py track2p_settings.cfg
"""

import configparser
import argparse
import numpy as np
from track2p.ops.default import DefaultTrackOps

# field -> type. 'float_or_none' handles iscell_thr specifically, since
# None ("manually curated") and a float (an actual probability threshold)
# are both valid and mean different things.
FIELDS = {
    'input_format': str,
    'reg_chan': int,
    'transform_type': str,
    'iscell_thr': 'float_or_none',
    'matching_method': str,
    'iou_dist_thr': int,
    'thr_remove_zeros': bool,
    'thr_method': str,
    'show_roi_reg_output': bool,
    'win_size': int,
    'sat_perc': float,
    'save_in_s2p_format': bool,
}


def _coerce(value_str, kind):
    value_str = value_str.strip()
    if kind == 'float_or_none':
        return None if value_str.lower() == 'none' else float(value_str)
    if kind is bool:
        return value_str.lower() in ('1', 'true', 'yes', 'on')
    return kind(value_str)


def _to_str(value):
    return 'None' if value is None else str(value)


def load_track_ops(cfg_path):
    """Returns a DefaultTrackOps() with settings from cfg_path applied.
    You still need to set all_ds_path and save_path yourself -- those are
    per-run, not part of this file."""
    parser = configparser.ConfigParser()
    read_ok = parser.read(cfg_path)
    if not read_ok:
        raise FileNotFoundError(f"Couldn't read config file: {cfg_path}")
    section = parser['track_ops']

    track_ops = DefaultTrackOps()
    for field, kind in FIELDS.items():
        if field in section:
            setattr(track_ops, field, _coerce(section[field], kind))
    return track_ops


def save_track_ops_cfg(track_ops_dict, cfg_path):
    """Writes a .cfg capturing just the FIELDS above, from a track_ops dict
    (e.g. np.load('track_ops.npy', allow_pickle=True).item())."""
    parser = configparser.ConfigParser()
    parser['track_ops'] = {
        field: _to_str(track_ops_dict[field]) for field in FIELDS if field in track_ops_dict
    }
    with open(cfg_path, 'w') as f:
        parser.write(f)
    print(f'Wrote {cfg_path}:\n')
    with open(cfg_path) as f:
        print(f.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cfg_path')
    ap.add_argument('--export', metavar='TRACK_OPS_NPY',
                     help='existing track_ops.npy to export settings FROM, writing cfg_path')
    args = ap.parse_args()

    if args.export:
        track_ops_dict = np.load(args.export, allow_pickle=True).item()
        save_track_ops_cfg(track_ops_dict, args.cfg_path)
    else:
        track_ops = load_track_ops(args.cfg_path)
        print(f'Settings in {args.cfg_path}:')
        for field in FIELDS:
            print(f'  {field}: {getattr(track_ops, field)!r}')


if __name__ == '__main__':
    main()
