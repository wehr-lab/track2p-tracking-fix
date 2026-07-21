"""
missing_session_histogram.py

Follow-up to compare_gap_vs_vanilla.py: for rows that are STILL incomplete
under the gap-tolerant match_mat (n_present < n_sessions), which specific
session index is missing most often? If one session index dominates
regardless of which row/chain you look at, that's evidence the problem is
that session's own data quality (fewer/worse detected ROIs, motion, focus,
etc.) rather than a registration-pairing issue that gap-jumping can route
around.

Usage:
    python missing_session_histogram.py /path/to/gap_tolerant/track2p [--min-present 5]

--min-present filters to rows that got reasonably far (default 5 of N) so
the histogram isn't dominated by ROIs that barely got going at all.

Session index -> calendar date / folder name is read from track_ops.npy's
all_ds_path (expected alongside plane{j}_match_mat.npy in save_path) so a
dominant-session flag can be chased down to an actual date without manually
cross-referencing session_order_utils output from a separate run.
"""

import sys
import os
import argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_order_utils import parse_session_date


def _load_session_labels(save_path, n_sessions):
    """Returns a list of n_sessions display labels ('MM-DD-YY  folder_name'),
    or None if track_ops.npy isn't there / doesn't match -- callers should
    fall back to plain session indices rather than fail outright."""
    ops_path = os.path.join(save_path, 'track_ops.npy')
    if not os.path.exists(ops_path):
        print(f'  (no track_ops.npy at {ops_path} -- showing session index only, no dates)')
        return None
    try:
        all_ds_path = np.load(ops_path, allow_pickle=True).item()['all_ds_path']
    except Exception as e:
        print(f'  (could not read all_ds_path from {ops_path}: {e!r} -- showing session index only)')
        return None
    if len(all_ds_path) != n_sessions:
        print(f'  (all_ds_path has {len(all_ds_path)} entries but match_mat has {n_sessions} sessions '
              f'-- mismatch, showing session index only)')
        return None

    labels = []
    for p in all_ds_path:
        name = os.path.basename(os.path.normpath(p))
        try:
            labels.append(f'{parse_session_date(p).isoformat()}  {name}')
        except ValueError:
            labels.append(name)
    return labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path')
    parser.add_argument('--min-present', type=int, default=5)
    args = parser.parse_args()

    nplanes = 0
    while os.path.exists(os.path.join(args.save_path, f'plane{nplanes}_match_mat.npy')):
        nplanes += 1

    for j in range(nplanes):
        mm = np.load(os.path.join(args.save_path, f'plane{j}_match_mat.npy'), allow_pickle=True)
        n_roi, n_sessions = mm.shape
        alive = (mm != None)  # noqa: E711
        n_present = alive.sum(axis=1)

        labels = _load_session_labels(args.save_path, n_sessions)

        incomplete = np.where((n_present < n_sessions) & (n_present >= args.min_present))[0]
        print(f'\nPlane {j}: {len(incomplete)} rows with {args.min_present}-{n_sessions-1} sessions present '
              f'(incomplete, but got reasonably far)')

        missing_counts = np.zeros(n_sessions, dtype=int)
        for roi_idx in incomplete:
            missing = np.where(~alive[roi_idx])[0]
            missing_counts[missing] += 1

        session_col_width = max((len(lbl) for lbl in labels), default=7) if labels else 7
        session_col_width = max(session_col_width, len('session'))
        print(f'  {"session":>{session_col_width}} {"missing in # rows":>20} {"% of incomplete rows":>22}')
        for s in range(n_sessions):
            pct = 100 * missing_counts[s] / len(incomplete) if len(incomplete) > 0 else 0
            flag = '  <-- dominant' if len(incomplete) > 0 and missing_counts[s] >= 0.5 * len(incomplete) else ''
            label = labels[s] if labels else str(s)
            print(f'  {label:>{session_col_width}} {missing_counts[s]:>20} {pct:>21.1f}%{flag}')


if __name__ == '__main__':
    main()
