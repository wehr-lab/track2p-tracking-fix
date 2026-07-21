"""
screen_sessions.py

Automated first-pass triage across ALL sessions in a track_ops.npy config,
combining the criteria worked out from manually finding wehr5336's two bad
sessions (02-03-26: low cell count + poor image; 02-24-26: normal cell
count, but both its neighboring transitions were degraded). Prints a
ranked suspect table instead of requiring you to eyeball every session's
mean image by hand.

TREAT THIS AS TRIAGE, NOT A VERDICT: always visually confirm (via
compare_session_qc.py) before excluding anything it flags. A flag can be
genuine biology (fewer active cells that day), not necessarily bad data.

Two signal types, checked independently:

INTRINSIC (per-session, works even before any track2p run):
  - cell count (iscell-filtered), robust z-score vs. the group
  - mean-image sharpness (variance of Laplacian -- a standard no-reference
    blur metric: lower = blurrier), robust z-score vs. the group

RELATIONAL (only unlocked once a track2p run already exists at save_path,
i.e. match_diagnostics.npy is present):
  - for each session, the average match rate (fraction of IOU values
    clearing threshold) of its neighboring pair(s). A session flagged here
    has BOTH its adjacent transitions degraded simultaneously -- a much
    more specific fingerprint for "this session is the shared cause" than
    just one noisy pair
  - (only if plane{j}_match_mat.npy is also present, from a completed
    chaining run) how often this session is the "still missing" one among
    otherwise-near-complete rows

Usage:
    python screen_sessions.py /path/to/save_path
    python screen_sessions.py /path/to/save_path --plane 0 --z-thresh 2.5
"""

import os
import argparse
import numpy as np
from scipy import ndimage


def robust_z(values):
    """Robust z-scores via median/MAD (MAD scaled to approximate std under
    normality). Returns all zeros if every value is identical (MAD=0)."""
    values = np.asarray(values, dtype=float)
    med = np.median(values)
    mad = np.median(np.abs(values - med))
    if mad == 0:
        return np.zeros_like(values)
    return 0.6745 * (values - med) / mad


def sharpness_metric(img):
    """Variance of the Laplacian -- higher = sharper/more in-focus,
    lower = blurrier. Standard no-reference blur metric."""
    return float(np.var(ndimage.laplace(img.astype(np.float64))))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path with track_ops.npy')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--z-thresh', type=float, default=2.5,
                         help='robust z-score magnitude to flag cell count / sharpness '
                              'as an outlier (default 2.5)')
    parser.add_argument('--match-rate-mads', type=float, default=1.5,
                         help='flag a session if ALL its neighboring pairs are this many '
                              'MADs below the median pair match rate (default 1.5)')
    args = parser.parse_args()
    plane = args.plane

    track_ops = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    all_ds_path = track_ops['all_ds_path']
    iscell_thr = track_ops.get('iscell_thr', 0.5)
    n_sessions = len(all_ds_path)
    labels = [os.path.basename(p) for p in all_ds_path]

    # ---- intrinsic signals: cell count + mean-image sharpness ----
    counts = []
    sharpness = []
    for ds_path in all_ds_path:
        iscell = np.load(os.path.join(ds_path, 'suite2p', f'plane{plane}', 'iscell.npy'), allow_pickle=True)
        if iscell_thr is None:
            n = int(np.sum(iscell[:, 0] == 1))
        else:
            n = int(np.sum(iscell[:, 1] > iscell_thr))
        counts.append(n)

        ops = np.load(os.path.join(ds_path, 'suite2p', f'plane{plane}', 'ops.npy'), allow_pickle=True).item()
        sharpness.append(sharpness_metric(ops['meanImg']))

    count_z = robust_z(counts)
    sharp_z = robust_z(sharpness)

    # ---- relational signal 1: neighboring-pair match rate (needs match_diagnostics.npy) ----
    pair_rate = None
    diag_path = os.path.join(args.save_path, 'match_diagnostics.npy')
    if os.path.exists(diag_path):
        diag = np.load(diag_path, allow_pickle=True).item()
        iou_values = diag['iou_values']
        thresholds = diag['thresholds']
        pair_rate = []
        for i in range(n_sessions - 1):
            iou_arr = np.asarray(iou_values[i][plane])
            thr = thresholds[i][plane]
            rate = float(np.mean(iou_arr >= thr)) if len(iou_arr) > 0 else float('nan')
            pair_rate.append(rate)
        pair_rate_med = float(np.median(pair_rate))
        pair_rate_mad = float(np.median(np.abs(np.array(pair_rate) - pair_rate_med)))
    else:
        print(f'(no match_diagnostics.npy at {args.save_path} -- skipping relational signals; '
              f'run track2p once first to unlock those)\n')

    # ---- relational signal 2: dominant-missing-session (needs plane{j}_match_mat.npy too) ----
    missing_pct = None
    mm_path = os.path.join(args.save_path, f'plane{plane}_match_mat.npy')
    if os.path.exists(mm_path) and pair_rate is not None:
        mm = np.load(mm_path, allow_pickle=True)
        alive = (mm != None)  # noqa: E711
        n_present = alive.sum(axis=1)
        incomplete = np.where((n_present < n_sessions) & (n_present >= max(1, n_sessions // 2)))[0]
        missing_pct = np.zeros(n_sessions)
        if len(incomplete) > 0:
            for roi_idx in incomplete:
                missing_pct[~alive[roi_idx]] += 1
            missing_pct = 100 * missing_pct / len(incomplete)

    # ---- assemble per-session report ----
    print(f'{n_sessions} sessions, plane {plane}\n')
    header = f'{"session":>8} {"label":<20} {"cells":>8} {"z":>6} {"sharpness":>12} {"z":>6}'
    if pair_rate is not None:
        header += f' {"neighbor rate":>14}'
    if missing_pct is not None:
        header += f' {"missing %":>10}'
    header += '  flags'
    print(header)

    suspects = []
    for s in range(n_sessions):
        flags = []
        if count_z[s] <= -args.z_thresh:
            flags.append('LOW_CELL_COUNT')
        if sharp_z[s] <= -args.z_thresh:
            flags.append('BLURRY')

        neighbor_rate_str = ''
        if pair_rate is not None:
            neighbor_pairs = [p for p in (s - 1, s) if 0 <= p < len(pair_rate)]
            neighbor_rates = [pair_rate[p] for p in neighbor_pairs]
            avg_rate = float(np.mean(neighbor_rates))
            neighbor_rate_str = f'{avg_rate:.1%}'
            if pair_rate_mad > 0 and all(
                r <= pair_rate_med - args.match_rate_mads * pair_rate_mad for r in neighbor_rates
            ):
                flags.append('BAD_NEIGHBOR_TRANSITIONS')

        missing_str = ''
        if missing_pct is not None:
            missing_str = f'{missing_pct[s]:.1f}%'
            if missing_pct[s] >= 50:
                flags.append('DOMINANT_MISSING_SESSION')

        row = f'{s:>8} {labels[s]:<20} {counts[s]:>8} {count_z[s]:>6.1f} ' \
              f'{sharpness[s]:>12.1f} {sharp_z[s]:>6.1f}'
        if pair_rate is not None:
            row += f' {neighbor_rate_str:>14}'
        if missing_pct is not None:
            row += f' {missing_str:>10}'
        row += '  ' + (', '.join(flags) if flags else '-')
        print(row)

        if flags:
            suspects.append((s, labels[s], flags))

    print('\n' + '=' * 70)
    if suspects:
        print(f'{len(suspects)} suspect session(s) -- look at these first:')
        for s, label, flags in suspects:
            print(f'  session {s} ({label}): {", ".join(flags)}')
        print('\nThese are triage candidates, not verdicts -- confirm visually with '
              'compare_session_qc.py before excluding anything.')
    else:
        print('No sessions flagged by these criteria.')


if __name__ == '__main__':
    main()
