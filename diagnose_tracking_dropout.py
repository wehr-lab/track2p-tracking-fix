"""
diagnose_tracking_dropout.py

Run this LOCALLY against an existing track2p output folder (no need to
upload the large track_ops.npy / suite2p ops.npy files to Claude).

It reads:
  - <save_path>/track_ops.npy            (local only, just for metadata)
  - <save_path>/plane{j}_match_mat.npy   (small; the actual tracking result)
  - <ds_path>/suite2p/plane{j}/iscell.npy (small; per-session detected-cell counts)

It computes, per plane:
  - how many ROIs are still "alive" (non-None) after each session
  - the empirical per-transition survival probability
  - a fit of the constant-p exponential decay model p^(n-1) against the
    actual survival curve, to test the "weakest link" hypothesis directly
  - where the biggest single-transition drops happen (candidate problem
    sessions / registration issues)

Output (small, safe to upload back):
  - tracking_diagnostics.json   summary stats per plane
  - tracking_diagnostics.png    survival curve + per-transition dropout plot

Usage:
    python diagnose_tracking_dropout.py /path/to/track2p/save_path
"""

import sys
import os
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

FONT_SIZE = 18
plt.rcParams.update({'font.size': FONT_SIZE})


def load_track_ops(save_path):
    """Load track_ops.npy locally just for path/plane metadata. Not uploaded anywhere."""
    ops_path = os.path.join(save_path, 'track_ops.npy')
    if not os.path.exists(ops_path):
        print(f'WARNING: could not find {ops_path}; proceeding without suite2p detection counts.')
        return None
    ops_dict = np.load(ops_path, allow_pickle=True).item()
    return ops_dict


def load_iscell_counts(all_ds_path, nplanes, iscell_thr):
    """Small per-session file; gives raw suite2p detected-cell counts for context."""
    counts = []
    for ds_path in all_ds_path:
        plane_counts = []
        for j in range(nplanes):
            iscell_path = os.path.join(ds_path, 'suite2p', f'plane{j}', 'iscell.npy')
            if not os.path.exists(iscell_path):
                plane_counts.append(None)
                continue
            iscell = np.load(iscell_path, allow_pickle=True)
            if iscell_thr is None:
                n = int(np.sum(iscell[:, 0] == 1))
            else:
                n = int(np.sum(iscell[:, 1] > iscell_thr))
            plane_counts.append(n)
        counts.append(plane_counts)
    return counts  # [n_sessions][n_planes]


def analyze_match_mat(match_mat):
    """
    match_mat: object array, shape (n_roi_session0, n_sessions), entries are
    int indices or None. Anchored at session 0 (per current track2p logic).
    """
    n_roi, n_sessions = match_mat.shape
    alive = (match_mat != None)  # noqa: E711  (elementwise, matches np object array semantics)

    # how many ROIs are still alive going into each session
    survival_counts = alive.sum(axis=0).tolist()

    # per-transition survival probability: P(alive at i+1 | alive at i)
    transition_p = []
    for i in range(n_sessions - 1):
        alive_at_i = alive[:, i].sum()
        alive_at_i_and_next = (alive[:, i] & alive[:, i + 1]).sum()
        p = float(alive_at_i_and_next) / float(alive_at_i) if alive_at_i > 0 else float('nan')
        transition_p.append(p)

    # final yield: strict AND across all sessions (matches track2p's n_tracked)
    n_tracked_all = int(np.all(alive, axis=1).sum())

    # geometric-mean transition survival probability (the "p" in p^(N-1))
    valid_p = [p for p in transition_p if not np.isnan(p) and p > 0]
    if valid_p:
        p_geomean = float(np.exp(np.mean(np.log(valid_p))))
    else:
        p_geomean = float('nan')

    # predicted yield under constant-p model, for comparison against actual survival_counts
    predicted_survival = [survival_counts[0] * (p_geomean ** i) for i in range(n_sessions)]

    return {
        'n_roi_session0': int(n_roi),
        'n_sessions': int(n_sessions),
        'survival_counts': survival_counts,
        'transition_survival_p': transition_p,
        'geomean_transition_p': p_geomean,
        'predicted_survival_constant_p': predicted_survival,
        'n_tracked_all_sessions': n_tracked_all,
        'final_yield_fraction': n_tracked_all / n_roi if n_roi > 0 else float('nan'),
    }


def make_plot(results_by_plane, iscell_counts, out_path):
    n_planes = len(results_by_plane)
    fig, axes = plt.subplots(n_planes, 2, figsize=(16, 6 * n_planes), squeeze=False)

    for j, res in enumerate(results_by_plane):
        n_sessions = res['n_sessions']
        x = np.arange(n_sessions)

        # left: actual vs constant-p predicted survival curve
        ax = axes[j][0]
        ax.plot(x, res['survival_counts'], 'o-', linewidth=3, markersize=10, label='Actual (chained)')
        ax.plot(x, res['predicted_survival_constant_p'], '--', linewidth=3,
                 label=f"Constant-p model (p={res['geomean_transition_p']:.3f})")
        if iscell_counts is not None:
            raw_counts = [iscell_counts[i][j] for i in range(len(iscell_counts))
                          if iscell_counts[i][j] is not None]
            if len(raw_counts) == n_sessions:
                ax.plot(x, raw_counts, ':', linewidth=3, color='gray', label='Raw detected cells (suite2p)')
        ax.set_xlabel('Session index')
        ax.set_ylabel('# ROIs alive (tracked so far)')
        ax.set_title(f'Plane {j}: survival curve')
        ax.legend(fontsize=FONT_SIZE - 4)

        # right: per-transition survival probability
        ax2 = axes[j][1]
        trans_x = np.arange(len(res['transition_survival_p']))
        ax2.bar(trans_x, res['transition_survival_p'])
        ax2.axhline(res['geomean_transition_p'], color='red', linestyle='--', linewidth=3,
                     label=f"geomean p={res['geomean_transition_p']:.3f}")
        ax2.set_xlabel('Transition (session i -> i+1)')
        ax2.set_ylabel('Survival probability')
        ax2.set_ylim(0, 1)
        ax2.set_title(f'Plane {j}: per-transition survival\n(low bars = problem sessions)')
        ax2.legend(fontsize=FONT_SIZE - 4)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f'Saved plot to {out_path}')


def main():
    if len(sys.argv) < 2:
        print('Usage: python diagnose_tracking_dropout.py /path/to/track2p/save_path')
        sys.exit(1)

    save_path = sys.argv[1]
    ops_dict = load_track_ops(save_path)

    if ops_dict is not None:
        nplanes = ops_dict.get('nplanes', 1)
        all_ds_path = ops_dict.get('all_ds_path', [])
        iscell_thr = ops_dict.get('iscell_thr', 0.5)
    else:
        # fall back: infer nplanes from files present
        nplanes = 0
        while os.path.exists(os.path.join(save_path, f'plane{nplanes}_match_mat.npy')):
            nplanes += 1
        all_ds_path = []
        iscell_thr = 0.5

    results_by_plane = []
    for j in range(nplanes):
        mm_path = os.path.join(save_path, f'plane{j}_match_mat.npy')
        if not os.path.exists(mm_path):
            print(f'WARNING: missing {mm_path}, skipping plane {j}')
            continue
        match_mat = np.load(mm_path, allow_pickle=True)
        res = analyze_match_mat(match_mat)
        results_by_plane.append(res)
        print(f"\nPlane {j}:")
        print(f"  ROIs in session 0: {res['n_roi_session0']}")
        print(f"  Final tracked (all sessions): {res['n_tracked_all_sessions']} "
              f"({100 * res['final_yield_fraction']:.1f}%)")
        print(f"  Geometric-mean per-transition survival p: {res['geomean_transition_p']:.3f}")
        print(f"  Per-transition survival: {[round(p, 3) for p in res['transition_survival_p']]}")

    iscell_counts = None
    if all_ds_path:
        try:
            iscell_counts = load_iscell_counts(all_ds_path, nplanes, iscell_thr)
        except Exception as e:
            print(f'Could not load iscell counts ({e}); continuing without them.')

    out_dir = os.path.join(save_path, 'diagnostics')
    os.makedirs(out_dir, exist_ok=True)

    json_path = os.path.join(out_dir, 'tracking_diagnostics.json')
    with open(json_path, 'w') as f:
        json.dump({'results_by_plane': results_by_plane}, f, indent=2)
    print(f'\nSaved summary to {json_path}')

    plot_path = os.path.join(out_dir, 'tracking_diagnostics.png')
    make_plot(results_by_plane, iscell_counts, plot_path)

    print(f'\nDone. Upload the contents of {out_dir} (both files are small) for the next step.')


if __name__ == '__main__':
    main()
