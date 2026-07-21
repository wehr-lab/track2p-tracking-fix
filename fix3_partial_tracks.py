"""
fix3_partial_tracks.py

Fix #3: partial-track reporting (>=K of N sessions), as a post-hoc filter on
track2p's own output. Does NOT touch registration or matching -- reads the
same plane{j}_match_mat.npy that get_all_pl_match_mat() already wrote to
save_path, so it works on results you've already computed.

track2p's own yield stat is a strict AND across every session:
    n_tracked = np.sum(np.all(pl_match_mat != None, axis=1))     # t2p.py / match/loop.py

That throws away every ROI that was matched in, say, 8 of 9 sessions just
because it missed one transition. This script instead reports, and exports,
cells present in >=K of N sessions for a range of K, so you can see how much
usable data the strict-AND rule is discarding and pick a K to work with.

Run LOCALLY against an existing track2p save_path (small files only, nothing
here needs elastix/registration and nothing large gets touched):

    python fix3_partial_tracks.py /path/to/track2p/save_path [--min-k-frac 0.5]

Outputs, into <save_path>/diagnostics/:
    partial_track_summary.json   recovery counts per K, per plane
    partial_track_recovery.png   yield-vs-K curve per plane
    plane{j}_match_mat_partial_K{K}.npy   filtered match_mat for the
                                          recommended K (see below), same
                                          (n_roi, n_sessions) object-array
                                          format as the original, just with
                                          rows that don't meet K zeroed out
                                          -- drop-in compatible with anything
                                          downstream that already reads
                                          plane{j}_match_mat.npy
    plane{j}_match_mat_partial_K{K}.csv   the same filtered match_mat as a
                                          CSV -- one row per surviving cell,
                                          one column per session (header
                                          session_0..session_{N-1}), values
                                          are the per-session ROI index or
                                          blank if missing that session; for
                                          opening in Excel/R/pandas without
                                          needing numpy's object-array pickle
    plane{j}_coverage_K{K}.csv   one row per surviving cell: session-0 ROI
                                  index, how many sessions it's present in,
                                  which specific sessions are missing

K selection: by default, picks the largest K such that recovered-cell-count
is at least 2x the strict-AND (K=N) count, as a reasonable default "useful
recovery" cutoff -- but the JSON/plot report every K so you can override via
--k.
"""

import sys
import os
import csv
import json
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

FONT_SIZE = 16
plt.rcParams.update({'font.size': FONT_SIZE})


def analyze_partial_tracks(match_mat, min_k_frac=0.5):
    """
    match_mat: object array (n_roi_session0, n_sessions), entries are int
    index or None (track2p's native format, anchored at session 0).

    Returns recovery counts for every K from n_sessions down to
    ceil(min_k_frac * n_sessions), plus the per-row presence counts needed
    to build filtered exports.
    """
    n_roi, n_sessions = match_mat.shape
    alive = (match_mat != None)  # noqa: E711
    n_present = alive.sum(axis=1)  # per-ROI count of sessions present in

    k_min = max(1, int(np.ceil(min_k_frac * n_sessions)))
    k_values = list(range(n_sessions, k_min - 1, -1))

    recovery = []
    for k in k_values:
        n_at_k = int(np.sum(n_present >= k))
        recovery.append({
            'k': k,
            'k_frac_of_n': round(k / n_sessions, 3),
            'n_cells': n_at_k,
        })

    n_strict_and = recovery[0]['n_cells']  # k == n_sessions entry
    assert recovery[0]['k'] == n_sessions

    # default K: largest K giving >=2x strict-AND recovery, else fall back
    # to k = n_sessions - 1 (miss at most one session) if that alone clears
    # the 2x bar isn't met anywhere, else just n_sessions-1 as a mild default
    recommended_k = None
    for entry in recovery:
        if n_strict_and == 0 or entry['n_cells'] >= 2 * n_strict_and:
            recommended_k = entry['k']
            break
    if recommended_k is None:
        recommended_k = max(k_min, n_sessions - 1)

    return {
        'n_roi_session0': int(n_roi),
        'n_sessions': int(n_sessions),
        'n_present_per_roi': n_present.tolist(),
        'recovery_by_k': recovery,
        'strict_and_count': n_strict_and,
        'recommended_k': recommended_k,
    }, n_present, alive


def export_filtered_match_mat(match_mat, n_present, alive, k, out_dir, plane_idx):
    keep_mask = n_present >= k
    filtered = np.full_like(match_mat, None)
    filtered[keep_mask, :] = match_mat[keep_mask, :]

    mm_path = os.path.join(out_dir, f'plane{plane_idx}_match_mat_partial_K{k}.npy')
    np.save(mm_path, filtered)

    n_sessions = match_mat.shape[1]
    keep_idx = np.where(keep_mask)[0]

    # CSV version of the filtered match_mat itself: one row per surviving
    # cell, one column per session, values are the per-session ROI index
    # (blank if that session is missing for this cell)
    mm_csv_path = os.path.join(out_dir, f'plane{plane_idx}_match_mat_partial_K{k}.csv')
    with open(mm_csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([f'session_{s}' for s in range(n_sessions)])
        for roi_idx in keep_idx:
            row = [match_mat[roi_idx, s] if alive[roi_idx, s] else '' for s in range(n_sessions)]
            writer.writerow(row)

    # coverage CSV: which sessions each surviving cell is missing
    csv_path = os.path.join(out_dir, f'plane{plane_idx}_coverage_K{k}.csv')
    with open(csv_path, 'w') as f:
        f.write('session0_roi_idx,n_sessions_present,missing_sessions\n')
        for roi_idx in keep_idx:
            missing = [str(s) for s in range(n_sessions) if not alive[roi_idx, s]]
            f.write(f'{roi_idx},{int(n_present[roi_idx])},"{";".join(missing)}"\n')

    print(f'  K={k}: exported {keep_mask.sum()} cells -> {mm_path}')
    print(f'         csv version -> {mm_csv_path}')
    print(f'         coverage detail -> {csv_path}')
    return mm_path, mm_csv_path, csv_path


def make_plot(results_by_plane, out_path):
    n_planes = len(results_by_plane)
    fig, axes = plt.subplots(1, n_planes, figsize=(7 * n_planes, 6), squeeze=False)
    axes = axes[0]

    for j, res in enumerate(results_by_plane):
        ax = axes[j]
        ks = [e['k'] for e in res['recovery_by_k']]
        counts = [e['n_cells'] for e in res['recovery_by_k']]
        ax.plot(ks, counts, 'o-', linewidth=3, markersize=8, color='steelblue')
        ax.axvline(res['n_sessions'], color='gray', linestyle=':', linewidth=2,
                   label=f"strict AND (K={res['n_sessions']}): {res['strict_and_count']} cells")
        ax.axvline(res['recommended_k'], color='red', linestyle='--', linewidth=2,
                   label=f"recommended K={res['recommended_k']}: "
                         f"{next(e['n_cells'] for e in res['recovery_by_k'] if e['k']==res['recommended_k'])} cells")
        ax.set_xlabel('K (min sessions present)')
        ax.set_ylabel('# cells recovered')
        ax.set_title(f'Plane {j}: yield vs. K\n({res["n_roi_session0"]} candidate ROIs from session 0)')
        ax.invert_xaxis()
        ax.legend(fontsize=FONT_SIZE - 4)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f'\nSaved plot to {out_path}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing plane{j}_match_mat.npy')
    parser.add_argument('--min-k-frac', type=float, default=0.5,
                         help='lowest K to report, as a fraction of n_sessions (default 0.5)')
    parser.add_argument('--k', type=int, default=None,
                         help='override the auto-recommended K for the exported filtered match_mat/CSV')
    args = parser.parse_args()

    save_path = args.save_path
    nplanes = 0
    while os.path.exists(os.path.join(save_path, f'plane{nplanes}_match_mat.npy')):
        nplanes += 1
    if nplanes == 0:
        print(f'No plane{{j}}_match_mat.npy found in {save_path}')
        sys.exit(1)

    out_dir = os.path.join(save_path, 'diagnostics')
    os.makedirs(out_dir, exist_ok=True)

    results_by_plane = []
    for j in range(nplanes):
        mm_path = os.path.join(save_path, f'plane{j}_match_mat.npy')
        match_mat = np.load(mm_path, allow_pickle=True)
        res, n_present, alive = analyze_partial_tracks(match_mat, min_k_frac=args.min_k_frac)
        results_by_plane.append(res)

        k = args.k if args.k is not None else res['recommended_k']

        print(f'\nPlane {j}: {res["n_roi_session0"]} candidate ROIs, {res["n_sessions"]} sessions')
        print(f'  strict-AND (K={res["n_sessions"]}): {res["strict_and_count"]} cells '
              f'({100*res["strict_and_count"]/res["n_roi_session0"]:.1f}%)')
        for entry in res['recovery_by_k']:
            marker = '  <- recommended' if entry['k'] == res['recommended_k'] else ''
            print(f"  K={entry['k']:>2} ({entry['k_frac_of_n']*100:.0f}% of sessions): "
                  f"{entry['n_cells']:>5} cells{marker}")

        export_filtered_match_mat(match_mat, np.array(n_present), np.array(alive), k, out_dir, j)

    json_path = os.path.join(out_dir, 'partial_track_summary.json')
    # drop the big per-roi array from the JSON dump to keep it small/readable;
    # it's already reflected in the per-K counts and in the exported CSV
    json_safe = [{k_: v for k_, v in res.items() if k_ != 'n_present_per_roi'} for res in results_by_plane]
    with open(json_path, 'w') as f:
        json.dump({'results_by_plane': json_safe}, f, indent=2)
    print(f'\nSaved summary to {json_path}')

    plot_path = os.path.join(out_dir, 'partial_track_recovery.png')
    make_plot(results_by_plane, plot_path)

    print(f'\nDone. Upload partial_track_summary.json + partial_track_recovery.png '
          f'(both small) for the next step.')


if __name__ == '__main__':
    main()
