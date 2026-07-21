"""
compare_session_qc.py

Python replacement for compare_session_qc.m -- same two checks (mean images
side by side, iscell count bar chart with below-median outliers flagged),
but reading straight from suite2p output via numpy, no MATLAB round trip
needed. compare_session_qc.m and export_session_qc.py (which only existed
to produce the .mat file compare_session_qc.m loaded) are left in place
unchanged for anyone who still wants the MATLAB-side view, but this is now
the standard path in track2p_fix_workflow.md step 2.

Reuses track_ops.npy from save_path (all_ds_path, iscell_thr, etc.) -- same
config as your actual run, no need to re-type session paths. Sorts
all_ds_path chronologically first (session_order_utils.ensure_chronological_order)
so the image grid and bar chart are always in date order regardless of
whatever order happened to get saved in track_ops.npy.

Usage:
    python compare_session_qc.py /path/to/existing/track2p/save_path
    python compare_session_qc.py /path/to/existing/track2p/save_path --sessions 6 7 8
    python compare_session_qc.py /path/to/existing/track2p/save_path --sessions 12-02-25 12-16-25

--sessions accepts EITHER 0-indexed integers OR date/substring session
names (same convention as inspect_registration_pair.py's --ref/--mov) --
mixing both in the same call is fine. Default: all sessions.

Output (both written to <save_path>/diagnostics/ unless --out-dir overrides it):
  session_qc_images.png -- mean image of each requested session, side by
                            side, 1st-99th percentile contrast per panel so
                            one dim/noisy session doesn't get washed out
                            relative to the others
  session_qc_counts.png -- bar chart of detected-cell (iscell) count for
                            EVERY session in track_ops.npy (not just the
                            ones in --sessions), with any session below 50%
                            of the group median highlighted in red

Never exclude a session on session_qc alone, or on screen_sessions.py /
registration_quality_scan.py's numeric flags alone -- this confirms the
mean image actually looks degraded or the cell count is genuinely low, but
it cannot reveal a registration/alignment problem between two sessions that
individually look fine (that's what inspect_registration_pair.py is for).
"""

import os
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from session_order_utils import load_all_ds_path, ensure_chronological_order
from registration_qc_utils import norm01 as _norm01


def _resolve_sessions(all_ds_path, specs):
    """Resolve a list of --sessions values to 0-indexed positions. Each spec is
    either a plain integer (used directly) or a date/substring matched against
    session folder names -- fails loudly if a substring doesn't match exactly
    one session, same convention as inspect_registration_pair.py."""
    labels = [os.path.basename(os.path.normpath(p)) for p in all_ds_path]
    resolved = []
    for spec in specs:
        try:
            resolved.append(int(spec))
            continue
        except ValueError:
            pass
        matches = [i for i, lbl in enumerate(labels) if spec in lbl]
        if len(matches) != 1:
            raise ValueError(
                f"--sessions value '{spec}' matched {len(matches)} session(s) by substring, "
                f"expected exactly 1: {[labels[i] for i in matches]}\n"
                f"Use a more specific date/substring, or the numeric 0-indexed position instead."
            )
        resolved.append(matches[0])
    return resolved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='existing track2p save_path (contains track_ops.npy)')
    parser.add_argument('--sessions', nargs='+', default=None,
                         help='sessions to show mean images for -- 0-indexed integers and/or '
                              'date/substrings, mixed freely (default: all sessions)')
    parser.add_argument('--plane', type=int, default=0, help='plane index (default 0)')
    parser.add_argument('--out-dir', default=None,
                         help='output directory (default: <save_path>/diagnostics/)')
    args = parser.parse_args()

    track_ops_dict = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    iscell_thr = track_ops_dict.get('iscell_thr', 0.5)
    plane = args.plane

    all_ds_path = ensure_chronological_order(load_all_ds_path(args.save_path))
    all_labels = [os.path.basename(os.path.normpath(p)) for p in all_ds_path]
    print(f'{len(all_ds_path)} sessions found in track_ops.npy (iscell_thr={iscell_thr})')

    sessions = _resolve_sessions(all_ds_path, args.sessions) if args.sessions is not None else list(range(len(all_ds_path)))

    out_dir = args.out_dir if args.out_dir is not None else os.path.join(args.save_path, 'diagnostics')
    os.makedirs(out_dir, exist_ok=True)

    # --- 1) mean images side by side -----------------------------------
    mean_imgs = []
    mean_labels = []
    for s in sessions:
        ops_path = os.path.join(all_ds_path[s], 'suite2p', f'plane{plane}', 'ops.npy')
        ops = np.load(ops_path, allow_pickle=True).item()
        mean_imgs.append(ops['meanImg'].astype(np.float64))
        mean_labels.append(all_labels[s])
        print(f'  session {s} ({mean_labels[-1]}): meanImg {ops["meanImg"].shape}')

    n_imgs = len(mean_imgs)
    ncols = int(np.ceil(np.sqrt(n_imgs)))
    nrows = int(np.ceil(n_imgs / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows), squeeze=False)
    for i in range(nrows * ncols):
        ax = axes[i // ncols, i % ncols]
        ax.axis('off')
        if i < n_imgs:
            ax.imshow(_norm01(mean_imgs[i]), cmap='gray')
            ax.set_title(mean_labels[i], fontsize=9)
    fig.suptitle('Mean images (1st-99th percentile contrast)', fontsize=12)
    plt.tight_layout()
    images_out = os.path.join(out_dir, 'session_qc_images.png')
    plt.savefig(images_out, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'\nSaved {os.path.abspath(images_out)}')

    # --- 2) detected cell counts across ALL sessions --------------------
    counts = []
    for ds_path in all_ds_path:
        iscell_path = os.path.join(ds_path, 'suite2p', f'plane{plane}', 'iscell.npy')
        iscell = np.load(iscell_path, allow_pickle=True)
        if iscell_thr is None:
            n = int(np.sum(iscell[:, 0] == 1))
        else:
            n = int(np.sum(iscell[:, 1] > iscell_thr))
        counts.append(n)
        print(f'  {os.path.basename(ds_path)}: {n} cells')

    counts = np.array(counts)
    med_count = float(np.median(counts))
    outliers = np.where(counts < 0.5 * med_count)[0]

    fig2, ax2 = plt.subplots(figsize=(max(9, 0.5 * len(counts)), 4.5))
    colors = ['#cc3333' if i in outliers else '#4d80cc' for i in range(len(counts))]
    ax2.bar(range(len(counts)), counts, color=colors)
    ax2.set_xticks(range(len(counts)))
    ax2.set_xticklabels(all_labels, rotation=45, ha='right', fontsize=8)
    ax2.set_ylabel('# detected cells (iscell)')
    ax2.set_title('Detected cell count per session (red = <50% of group median)')
    ax2.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    counts_out = os.path.join(out_dir, 'session_qc_counts.png')
    plt.savefig(counts_out, dpi=150, bbox_inches='tight')
    plt.close(fig2)
    print(f'Saved {os.path.abspath(counts_out)}')

    if len(outliers) > 0:
        print(f'\nSessions with <50% of median cell count (potential quality issue, median={med_count:.0f}):')
        for i in outliers:
            print(f'  {all_labels[i]}: {counts[i]} cells')
    else:
        print(f'\nNo session is below 50% of the median cell count ({med_count:.0f}).')


if __name__ == '__main__':
    main()
