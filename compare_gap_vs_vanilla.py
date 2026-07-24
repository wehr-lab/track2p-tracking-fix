"""
compare_gap_vs_vanilla.py

Diagnostic: compare a vanilla-track2p plane{j}_match_mat.npy against the
plane{j}_match_mat.npy produced by run_t2p_gap_tolerant(), row by row --
not just the final strict-AND count, which can hide "helped some rows but
not enough to flip the total" behind an unchanged number.

Because vanilla track2p's chaining is a greedy forward loop that stops
permanently at the FIRST failed transition, every row's "sessions present"
count in the vanilla match_mat is always a contiguous run starting at
session 0 -- there's no way to be present in session 0, absent in session 3,
present again in session 4 under vanilla. Gap-tolerant chaining is the only
one of the two that CAN produce that pattern (a genuine hole mid-track).
This script checks:

  1. Did n_present increase for ANY row at all (even if it didn't flip
     enough rows to change the strict-AND total)?
  2. For rows that were 1 session short of full (n_present == n_sessions-1)
     under vanilla -- the easiest possible rescue -- did gap-tolerant
     actually complete any of them to n_sessions?
  3. Are the exact strict-AND cells identical between the two runs, or
     different cells that happen to add up to the same count?
  4. Did gap-tolerant ever produce a genuine mid-track hole (proof the gap
     logic executed and changed the pattern), or does every row still look
     like a contiguous vanilla-style run (suggesting the gap fallback
     never actually kicked in)?

Usage:
    python compare_gap_vs_vanilla.py /path/to/vanilla/track2p /path/to/gap_tolerant/track2p
(each path should directly contain plane{j}_match_mat.npy)
"""

import sys
import os
import numpy as np


def load_planes(save_path):
    planes = []
    j = 0
    while os.path.exists(os.path.join(save_path, f'plane{j}_match_mat.npy')):
        planes.append(np.load(os.path.join(save_path, f'plane{j}_match_mat.npy'), allow_pickle=True))
        j += 1
    return planes


def is_contiguous_prefix(alive_row):
    """True if alive_row looks like [True]*k + [False]*(n-k) -- vanilla's signature shape."""
    n_present = alive_row.sum()
    return bool(np.all(alive_row[:n_present]) and not np.any(alive_row[n_present:]))


def compare(vanilla_mm, gap_mm, plane_idx):
    assert vanilla_mm.shape == gap_mm.shape, f'plane{plane_idx}: shape mismatch {vanilla_mm.shape} vs {gap_mm.shape}'
    n_roi, n_sessions = vanilla_mm.shape

    van_alive = (vanilla_mm != None)  # noqa: E711
    gap_alive = (gap_mm != None)  # noqa: E711
    van_present = van_alive.sum(axis=1)
    gap_present = gap_alive.sum(axis=1)

    improved = np.sum(gap_present > van_present)
    worsened = np.sum(gap_present < van_present)
    unchanged = np.sum(gap_present == van_present)

    van_strict9 = set(np.where(van_present == n_sessions)[0].tolist())
    gap_strict9 = set(np.where(gap_present == n_sessions)[0].tolist())

    near_miss_van = np.where(van_present == n_sessions - 1)[0]  # 1 session short under vanilla
    n_near_miss = len(near_miss_van)
    n_near_miss_completed = int(np.sum(gap_present[near_miss_van] == n_sessions))

    n_holes = 0  # rows where gap-tolerant produced a genuine mid-track gap (proof gap logic fired)
    for roi_idx in range(n_roi):
        if gap_present[roi_idx] > 0 and not is_contiguous_prefix(gap_alive[roi_idx]):
            n_holes += 1

    print(f'\n=== Plane {plane_idx} ({n_roi} candidate ROIs, {n_sessions} sessions) ===')
    print(f'  strict-AND({n_sessions}) vanilla: {len(van_strict9)}   gap-tolerant: {len(gap_strict9)}')
    print(f'  same exact cells: {van_strict9 == gap_strict9}   '
          f'(overlap: {len(van_strict9 & gap_strict9)}, vanilla-only: {len(van_strict9 - gap_strict9)}, '
          f'gap-only: {len(gap_strict9 - van_strict9)})')
    print(f'  rows with MORE sessions present under gap-tolerant: {improved}')
    print(f'  rows with FEWER sessions present under gap-tolerant: {worsened} (should be 0 -- flag if not)')
    print(f'  rows unchanged: {unchanged}')
    print(f'  rows 1-session-short under vanilla (easiest rescue case): {n_near_miss}')
    print(f'  ...of those, how many gap-tolerant completed to all {n_sessions}: {n_near_miss_completed}')
    print(f'  rows with a genuine mid-track hole under gap-tolerant '
          f'(proof the gap-jump logic actually fired and changed the pattern): {n_holes}')

    if improved == 0 and n_holes == 0:
        print('  ==> gap-tolerant produced a BYTE-FOR-BYTE vanilla-shaped result on this plane.')
        print('      That is consistent with the gap fallback never actually triggering (a bug),')
        print('      not with "gap registration was tried and failed" -- worth checking your run')
        print('      log for "[gap] registered session X -> session Y" lines to confirm either way.')

    if n_near_miss > 0 and n_near_miss_completed == 0:
        print(f'  ==> 0 of {n_near_miss} near-miss row(s) rescued -- this is EXPECTED, not diagnostic of')
        print('      a problem. Vanilla chaining is forward-only/permanently-truncating, so being exactly')
        print('      1 session short under vanilla can ONLY mean missing the LAST session in the list --')
        print('      and gap-tolerant can never rescue a last-session failure, at any max_gap, since there')
        print('      is no downstream session left to skip forward onto. This will read 0 on every')
        print('      dataset; do not read it as evidence of a weak transition or an undersized max_gap.')
        if n_holes > 0:
            print(f'      Gap-tolerant DID fire elsewhere ({n_holes} row(s) with a genuine mid-track hole) --')
            print('      if THOSE still fall short of strict-AND, that is the thread worth pulling: check')
            print('      screen_sessions.py / registration_quality_scan.py / missing_session_histogram.py')
            print('      on this run for a specific mid-chain session responsible.')


def main():
    if len(sys.argv) < 3:
        print('Usage: python compare_gap_vs_vanilla.py /path/to/vanilla/track2p /path/to/gap_tolerant/track2p')
        sys.exit(1)

    vanilla_planes = load_planes(sys.argv[1])
    gap_planes = load_planes(sys.argv[2])

    if len(vanilla_planes) == 0:
        print(f'No plane{{j}}_match_mat.npy found in {sys.argv[1]}')
        sys.exit(1)
    if len(gap_planes) == 0:
        print(f'No plane{{j}}_match_mat.npy found in {sys.argv[2]}')
        sys.exit(1)
    if len(vanilla_planes) != len(gap_planes):
        print(f'WARNING: {len(vanilla_planes)} planes in vanilla vs {len(gap_planes)} in gap-tolerant -- '
              f'comparing the overlap only')

    for j in range(min(len(vanilla_planes), len(gap_planes))):
        compare(vanilla_planes[j], gap_planes[j], j)


if __name__ == '__main__':
    main()
