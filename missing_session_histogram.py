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
"""

import sys
import os
import argparse
import numpy as np


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

        incomplete = np.where((n_present < n_sessions) & (n_present >= args.min_present))[0]
        print(f'\nPlane {j}: {len(incomplete)} rows with {args.min_present}-{n_sessions-1} sessions present '
              f'(incomplete, but got reasonably far)')

        missing_counts = np.zeros(n_sessions, dtype=int)
        for roi_idx in incomplete:
            missing = np.where(~alive[roi_idx])[0]
            missing_counts[missing] += 1

        print(f'  {"session":>8} {"missing in # rows":>20} {"% of incomplete rows":>22}')
        for s in range(n_sessions):
            pct = 100 * missing_counts[s] / len(incomplete) if len(incomplete) > 0 else 0
            flag = '  <-- dominant' if len(incomplete) > 0 and missing_counts[s] >= 0.5 * len(incomplete) else ''
            print(f'  {s:>8} {missing_counts[s]:>20} {pct:>21.1f}%{flag}')


if __name__ == '__main__':
    main()
