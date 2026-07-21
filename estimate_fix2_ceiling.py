"""
estimate_fix2_ceiling.py

Cheap, no-new-registration estimate of how much fix #2 (seed chains from
ANY session, not just session 0) might realistically add on top of
fix #1 (gap-tolerant chaining) + fix #3 (partial-track reporting) --
before spending the effort to actually implement it.

Reuses two things you already have on disk, no elastix/matching needed:
  - plane{j}_match_mat.npy   -- to find "orphan" ROIs: cells detected in a
    session but never claimed by ANY session-0-anchored chain (i.e. they
    have no representation anywhere in match_mat). These are exactly the
    population fix #2 would try to recover by seeding new chains from
    sessions other than 0.
  - match_diagnostics.npy    -- per-transition IOU distributions + Otsu
    thresholds, to get the empirically observed "fraction of ROIs that
    survive this specific transition" rate, used to project how many
    orphans might realistically chain forward for several more sessions
    (vs. just being one-off, unmatchable detections / noise).

IMPORTANT CAVEAT -- read before trusting these numbers: orphan pools at
DIFFERENT sessions likely overlap heavily. A real cell invisible to the
current session-0-anchored scheme will typically show up as an "orphan" at
EVERY session it's actually present in, not just one. So the per-session
numbers below are NOT additive -- summing them would badly overcount.
Read each row as "ceiling if you anchored fresh chains starting at this
session," and treat the single largest projected-survival value as a rough
upper bound on net-new cells a full multi-anchor fix #2 could add. True
deduplication across anchors needs an actual implementation to resolve.

Usage:
    python estimate_fix2_ceiling.py /path/to/gap_tolerant/track2p
"""

import os
import argparse
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path')
    parser.add_argument('--plane', type=int, default=0)
    args = parser.parse_args()
    plane = args.plane

    track_ops = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    all_ds_path = track_ops['all_ds_path']
    iscell_thr = track_ops.get('iscell_thr', 0.5)
    n_sessions = len(all_ds_path)

    mm_path = os.path.join(args.save_path, f'plane{plane}_match_mat.npy')
    match_mat = np.load(mm_path, allow_pickle=True)

    diag_path = os.path.join(args.save_path, 'match_diagnostics.npy')
    if not os.path.exists(diag_path):
        raise FileNotFoundError(
            f'{diag_path} not found -- this analysis needs the per-transition IOU data. '
            f'Point this at a save_path produced by a run that included save_match_diagnostics() '
            f'(any run_t2p/run_t2p_gap_tolerant call using your patched track2p should have this).'
        )
    diag = np.load(diag_path, allow_pickle=True).item()
    iou_values = diag['iou_values']   # [pair][plane] -> 1D array of IOUs
    thresholds = diag['thresholds']   # [pair][plane] -> float

    # empirical per-transition survival rate: fraction of that pair's candidate
    # matches that cleared its own Otsu/min threshold -- pair i is the
    # transition session i -> session i+1
    pair_survival = []
    for i in range(n_sessions - 1):
        iou_arr = np.asarray(iou_values[i][plane])
        thr = thresholds[i][plane]
        rate = float(np.mean(iou_arr >= thr)) if len(iou_arr) > 0 else float('nan')
        pair_survival.append(rate)

    print(f'{n_sessions} sessions, plane {plane}')
    print(f'\nPer-transition survival rate (fraction of candidates clearing threshold), '
          f'from match_diagnostics.npy:')
    for i, r in enumerate(pair_survival):
        print(f'  pair {i} (session {i} -> {i + 1}): {r:.1%}')

    # total iscell-filtered cell count per session (same convention used
    # everywhere else in this pipeline)
    total_cells = []
    for ds_path in all_ds_path:
        iscell = np.load(os.path.join(ds_path, 'suite2p', f'plane{plane}', 'iscell.npy'), allow_pickle=True)
        if iscell_thr is None:
            n = int(np.sum(iscell[:, 0] == 1))
        else:
            n = int(np.sum(iscell[:, 1] > iscell_thr))
        total_cells.append(n)

    print(f'\n{"session":>8} {"total cells":>12} {"claimed":>10} {"orphans":>10} '
          f'{"orphan %":>10} {"proj. survive to end":>24}')

    for i in range(1, n_sessions):  # session 0 has no orphans by construction (every
                                      # session-0 ROI already gets a match_mat row)
        col = match_mat[:, i]
        claimed = set(v for v in col if v is not None)
        orphans = total_cells[i] - len(claimed)
        orphan_pct = 100 * orphans / total_cells[i] if total_cells[i] > 0 else 0

        # project how many of these orphans would survive if chained FORWARD
        # from session i using the SAME empirically observed per-transition
        # rates (pairs i, i+1, ..., n_sessions-2)
        if i < n_sessions - 1:
            cum_p = 1.0
            for j in range(i, n_sessions - 1):
                cum_p *= pair_survival[j]
            projected = orphans * cum_p
            proj_str = f'{projected:.0f} ({100 * cum_p:.1f}% of orphans)'
        else:
            proj_str = 'n/a (last session)'

        print(f'{i:>8} {total_cells[i]:>12} {len(claimed):>10} {orphans:>10} '
              f'{orphan_pct:>9.1f}% {proj_str:>24}')

    print('\nCAVEAT: these per-session numbers are NOT additive -- a real cell invisible to the '
          'current session-0-anchored scheme typically shows up as an orphan at MULTIPLE sessions, '
          'not just one, so summing the rows above would double/triple count the same cells. Read '
          'each row as "ceiling if you anchored fresh chains starting at this session," and treat '
          'the single largest projected-survival value as a rough upper bound on net-new cells a '
          'full multi-anchor fix #2 could add. True dedup across anchors needs an actual '
          'implementation to resolve -- this is a bound, not a prediction.')


if __name__ == '__main__':
    main()
