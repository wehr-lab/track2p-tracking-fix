"""
registration_quality_scan.py

Quantitative companion to inspect_registration_pair.py: computes the same
SSIM (structural similarity) alignment score for EVERY consecutive session
pair in the list, not just one you point it at, so a specific pair's score
can be judged against the actual distribution instead of a single
anecdotal "control" pair -- "visibly more red/green than pair X" is a
reasonable starting instinct, but it's still an eyeball comparison to one
other data point. This gives you the full column and flags outliers with
the same robust (median/MAD) z-score approach screen_sessions.py already
uses for cell count and sharpness.

This is a genuinely different signal from screen_sessions.py's "neighbor
rate" column: neighbor rate comes from the downstream IOU-based ROI
matching (how many detected cells matched above threshold), while SSIM
here measures raw IMAGE-level alignment quality directly, before any ROI
detection/matching happens. They usually agree, but SSIM can catch a
registration problem even in a session with few detected ROIs (where the
IOU-based neighbor rate has little to work with), and vice versa.

Registers every (i, i+1) pair with the SAME elastix call your real pipeline
uses (track2p.register.elastix.reg_img_elastix) -- this is N-1 real
registrations, the same cost as one run_gap_tolerant.py's initial
consecutive pass, so expect it to take a while on a large session list.

Also writes a grid PNG (one row per pair) so you can screen the whole
session list visually in one image instead of opening N-1 separate
inspect_registration_pair.py outputs. Each row shows:
  1. ref image (raw)
  2. mov image, BEFORE registration onto ref (raw) -- shows the two
     panels are the SAME registration inspect_registration_pair.py would
     run; kept because it lets you tell "genuinely different-looking raw
     data" apart from "registration algorithm failed on an easy pair" at
     a glance, which the overlay alone can't distinguish (the overlay's
     green channel already IS the after-registration image, so a
     separate after-reg panel would mostly repeat that same information).
     Switch to the after-reg image instead with --middle-panel mov_reg if
     you'd rather see that.
  3. red/green overlay of ref (red) vs. registered mov (green) -- same
     convention as inspect_registration_pair.py. Well-aligned structures
     appear yellow/white; misaligned structures show up as separated
     red/green fringes.
Rows whose SSIM is flagged as a low-alignment outlier get a red row label
and a red border around their panels, so a bad pair jumps out while
scrolling the full-list image. Use inspect_registration_pair.py on any
pair this flags for the full 4-panel single-pair view (also gives you the
BEFORE and AFTER mov panels together, which this grid deliberately
doesn't -- that's the trade for fitting the whole session list in one
image).

Usage:
    python registration_quality_scan.py /path/to/track2p/save_path
    python registration_quality_scan.py /path/to/track2p/save_path --z-thresh 2.0
    python registration_quality_scan.py /path/to/track2p/save_path --middle-panel mov_reg
    python registration_quality_scan.py /path/to/track2p/save_path --no-grid   # table only, skip the PNG

IMPORTANT -- which track2p gets imported depends on where you run this
from; see the same note in run_gap_tolerant.py. The sys.path.insert below
forces the git clone to win regardless of cwd.
"""

import sys
GIT_CLONE_PATH = '/Users/wehr/git/track2p'   # confirm with: python -c "import track2p; print(track2p.__file__)"
sys.path.insert(0, GIT_CLONE_PATH)

import os
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from track2p.ops.default import DefaultTrackOps
from track2p.register.elastix import reg_img_elastix

from screen_sessions import robust_z  # same median/MAD z-score used for cell count / sharpness
from registration_qc_utils import load_mean_img as _load_mean_img, norm01 as _norm01, signal_mask, masked_ssim


def _build_grid(pairs_data, labels, scores, z, z_thresh, middle_panel, plane, out_path, panel_size, dpi):
    """pairs_data[i] = (ref_n, mov_raw_n, mov_reg_n, overlay) for pair i -> i+1."""
    n_pairs = len(pairs_data)
    fig, axes = plt.subplots(n_pairs, 3, figsize=(3 * panel_size, n_pairs * panel_size),
                              squeeze=False)

    col_titles = ['ref (raw)',
                   'mov, BEFORE reg (raw)' if middle_panel == 'mov_raw' else 'mov, AFTER reg',
                   'overlay: red=ref, green=reg mov']

    for i in range(n_pairs):
        ref_n, mov_raw_n, mov_reg_n, overlay = pairs_data[i]
        mid_n = mov_raw_n if middle_panel == 'mov_raw' else mov_reg_n
        ax_ref, ax_mid, ax_ov = axes[i]

        ax_ref.imshow(ref_n, cmap='gray')
        ax_mid.imshow(mid_n, cmap='gray')
        ax_ov.imshow(overlay)

        flagged = z[i] <= -z_thresh
        color = 'red' if flagged else 'black'
        weight = 'bold' if flagged else 'normal'

        row_label = (f'{i}→{i+1}\n{labels[i]}\n→{labels[i+1]}\n'
                     f'SSIM={scores[i]:.3f}\nz={z[i]:.1f}' + ('\nLOW_ALIGNMENT' if flagged else ''))
        ax_ref.set_ylabel(row_label, rotation=0, ha='right', va='center', fontsize=7.5,
                           color=color, fontweight=weight, labelpad=8)

        for ax in (ax_ref, ax_mid, ax_ov):
            ax.set_xticks([])
            ax.set_yticks([])
            if flagged:
                for spine in ax.spines.values():
                    spine.set_edgecolor('red')
                    spine.set_linewidth(3)

        if i == 0:
            ax_ref.set_title(col_titles[0], fontsize=9)
            ax_mid.set_title(col_titles[1], fontsize=9)
            ax_ov.set_title(col_titles[2], fontsize=9)

    n_flagged = int(np.sum(z <= -z_thresh))
    fig.suptitle(f'registration_quality_scan.py -- plane {plane} -- {n_pairs} pair(s), '
                 f'{n_flagged} flagged at |z|>={z_thresh} (red)', fontsize=11, y=1.0)
    plt.tight_layout(rect=[0, 0, 1, 0.99])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing track_ops.npy')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--z-thresh', type=float, default=2.0,
                         help='robust z-score magnitude to flag a pair\'s SSIM as an outlier (default 2.0)')
    parser.add_argument('--no-grid', action='store_true',
                         help='skip building the grid PNG, just print the table (faster on a long session list '
                              'if you only want the numbers)')
    parser.add_argument('--middle-panel', choices=['mov_raw', 'mov_reg'], default='mov_raw',
                         help='which mov image to show in the grid\'s middle column -- raw (BEFORE registration, '
                              'default) or registered (AFTER). See module docstring for the trade-off; the overlay '
                              'column already shows the after-reg image via its green channel.')
    parser.add_argument('--grid-out', default=None,
                         help='output PNG path for the grid (default: <save_path>/diagnostics/registration_quality_grid.png)')
    parser.add_argument('--panel-size', type=float, default=3.2, help='inches per panel (default 3.2)')
    parser.add_argument('--dpi', type=int, default=100, help='grid PNG dpi (default 100)')
    args = parser.parse_args()

    track_ops = DefaultTrackOps()
    track_ops_dict = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    track_ops.from_dict(track_ops_dict)
    all_ds_path = track_ops.all_ds_path
    n_sessions = len(all_ds_path)
    labels = [os.path.basename(os.path.normpath(p)) for p in all_ds_path]

    print(f'Registering {n_sessions - 1} consecutive pair(s), plane {args.plane} -- this runs real '
          f'elastix registration for each, same cost as a normal run\'s consecutive pass...\n')

    scores = []
    pairs_data = []  # (ref_n, mov_raw_n, mov_reg_n, overlay) per pair, only kept if grid is being built
    for i in range(n_sessions - 1):
        ref_img = _load_mean_img(all_ds_path[i], args.plane)
        mov_img = _load_mean_img(all_ds_path[i + 1], args.plane)
        mov_img_reg, _ = reg_img_elastix(ref_img, mov_img, track_ops)
        mask = signal_mask(ref_img)
        ref_n = _norm01(ref_img)
        mov_reg_n = _norm01(mov_img_reg)
        score = masked_ssim(ref_n, mov_reg_n, mask)
        scores.append(score)
        print(f'  [{i + 1}/{n_sessions - 1}] {labels[i]} -> {labels[i + 1]}: SSIM={score:.3f}')

        if not args.no_grid:
            mov_raw_n = _norm01(mov_img)
            overlay = np.zeros((*ref_img.shape, 3))
            overlay[..., 0] = ref_n
            overlay[..., 1] = mov_reg_n
            pairs_data.append((ref_n, mov_raw_n, mov_reg_n, overlay))

    z = robust_z(scores)

    print(f'\n{"pair":>12}  {"ref":<16} {"mov":<16} {"SSIM":>7} {"z":>6}  flag')
    suspects = []
    for i in range(n_sessions - 1):
        flag = ''
        if z[i] <= -args.z_thresh:
            flag = '<-- LOW_ALIGNMENT'
            suspects.append((i, i + 1, labels[i], labels[i + 1], scores[i], z[i]))
        print(f'  {i:>3}->{i+1:<3}  {labels[i]:<16} {labels[i+1]:<16} {scores[i]:>7.3f} {z[i]:>6.1f}  {flag}')

    print('\n' + '=' * 70)
    if suspects:
        print(f'{len(suspects)} pair(s) with anomalously low registration alignment:')
        for i, k, ref_lbl, mov_lbl, score, zscore in suspects:
            print(f'  {i}->{k} ({ref_lbl} -> {mov_lbl}): SSIM={score:.3f} (z={zscore:.1f})')
        print('\nA session showing up in TWO flagged pairs (both its neighbor transitions) is much')
        print('stronger evidence than one flagged pair alone -- cross-reference against')
        print('screen_sessions.py\'s BAD_NEIGHBOR_TRANSITIONS flag before excluding anything.')
    else:
        print('No pairs flagged -- registration alignment looks consistent across the whole list.')

    if not args.no_grid:
        out_path = args.grid_out if args.grid_out is not None else os.path.join(
            args.save_path, 'diagnostics', 'registration_quality_grid.png')
        _build_grid(pairs_data, labels, scores, z, args.z_thresh, args.middle_panel, args.plane,
                    out_path, args.panel_size, args.dpi)
        print(f'\nSaved grid PNG: {os.path.abspath(out_path)}')
        print('One row per pair -- ref / mov-before-reg / overlay by default (--middle-panel mov_reg to swap the '
              'middle column). Flagged rows (|z| >= threshold) have a red label and red panel borders.')


if __name__ == '__main__':
    main()
