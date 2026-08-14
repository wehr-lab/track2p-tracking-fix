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

Flags a pair two ways, either of which is enough to flag it: a robust
(median/MAD) z-score outlier (--z-thresh, default 2.0), OR an absolute SSIM
floor (--ssim-floor, default 0.3). The z-score alone is NOT enough on a
heavily contaminated list -- it's relative to the CURRENT list's own
median/MAD, and if several sessions in the list are simultaneously bad
(e.g. scanning a raw, not-yet-cleaned session list), their low scores drag
the median down and inflate the MAD, so even a badly misaligned pair can
compute to an unremarkable z-score ("outlier masking" -- the same failure
mode that motivated building this SSIM scanner in the first place, since
Otsu-based neighbor rate has the analogous per-pair version of this
problem). The 0.3 default floor is calibrated against every pair this
project has actually visually confirmed as bad so far (masked SSIM
0.041-0.150) vs. every pair that wasn't (0.327-0.811) -- there's a real gap
between those two clusters and 0.3 sits in it. Like the z-score flag, a
floor flag is a trigger for visual confirmation via
inspect_registration_pair.py, not an auto-exclude verdict -- masked SSIM's
absolute calibration still isn't fully trusted (see registration_qc_utils.py).

Any flagged pair also gets a cheap follow-up check: an FFT phase-
correlation shift estimate (registration_qc_utils.phase_correlation_shift),
which -- unlike elastix's gradient-descent optimizer -- isn't sensitive to
displacement magnitude, so it can recover a large shift elastix's default
optimizer couldn't find from its starting point ("capture-range failure").
If phase correlation scores notably better than elastix did on a flagged
pair, that's a real, previously-unsuspected explanation worth knowing
before you conclude the pair is genuinely misaligned: the underlying data
may line up fine, elastix's search just failed. This surfaced for real on
wehr5917's session 6->7 pair (elastix SSIM 0.031, phase-corr SSIM 0.591,
64px recovered shift, confirmed visually) -- see debug_large_displacement.py
for the single-pair deep-dive version of this same check. Only run for
FLAGGED pairs, not the whole list, since it's only informative once
something already looks wrong -- pass --no-phase-corr-check to skip it.

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
  4. IOU histogram for that transition (only if match_diagnostics.npy
     exists at save_path, i.e. a track2p run -- vanilla or gap-tolerant --
     already happened here; this script can otherwise run standalone
     before any track2p run, so this column is a blank "not available yet"
     placeholder in that case). Otsu/min threshold drawn as a dashed red
     vertical line, match rate (fraction of candidate IOUs clearing it,
     same number screen_sessions.py's "neighbor rate" column reports) in
     the row label. This is a DIFFERENT signal from panels 1-3: SSIM there
     measures raw image-level alignment before any ROI detection/matching;
     this measures the downstream IOU distribution ROI matching actually
     produces. The point of putting it in the same row is to eyeball
     bimodality (clean separation between a "real match" and "different
     cell" peak) directly against that pair's image alignment -- a
     collapsed/unimodal histogram next to an otherwise-fine-looking
     overlay is a sign the Otsu threshold on that pair can't be trusted
     even though registration itself looks okay.
Rows whose SSIM is flagged as a low-alignment outlier get a red row label
and a red border around their panels, so a bad pair jumps out while
scrolling the full-list image. Use inspect_registration_pair.py on any
pair this flags for the full 4-panel single-pair view (also gives you the
BEFORE and AFTER mov panels together, which this grid deliberately
doesn't -- that's the trade for fitting the whole session list in one
image).

Usage:
    python registration_quality_scan.py /path/to/track2p/save_path
    python registration_quality_scan.py /path/to/track2p/save_path --z-thresh 2.0 --ssim-floor 0.3
    python registration_quality_scan.py /path/to/track2p/save_path --middle-panel mov_reg
    python registration_quality_scan.py /path/to/track2p/save_path --no-grid   # table only, skip the PNG

Also runnable together with screen_sessions.py in one call via
screen_and_scan.py, since in practice you always want both -- see that
script's docstring.

IMPORTANT -- which track2p gets imported depends on where you run this
from; see the same note in run_gap_tolerant.py. The sys.path.insert below
forces the git clone to win regardless of cwd.
"""

import sys
from machine_config import GIT_CLONE_PATH   # per-machine path -- see machine_config.py / local_machine.cfg
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
from session_order_utils import load_all_ds_path, ensure_chronological_order
from registration_qc_utils import (load_mean_img as _load_mean_img, norm01 as _norm01, signal_mask,
                                    masked_ssim, phase_correlation_shift)


def _build_grid(pairs_data, labels, scores, z, flagged, z_thresh, ssim_floor, middle_panel, plane, out_path, panel_size, dpi, phase_corr_info=None, iou_data=None):
    """pairs_data[i] = (ref_n, mov_raw_n, mov_reg_n, overlay) for pair i -> i+1.

    iou_data[i] = (iou_arr, thr, rate) or None (no match_diagnostics.npy, or
    that pair had zero candidate IOUs) -- adds a 4th "IOU histogram" column
    when iou_data is passed at all (i.e. match_diagnostics.npy existed),
    even if individual rows within it are None."""
    n_pairs = len(pairs_data)
    n_cols = 4 if iou_data is not None else 3
    fig, axes = plt.subplots(n_pairs, n_cols, figsize=(n_cols * panel_size, n_pairs * panel_size),
                              squeeze=False)

    col_titles = ['ref (raw)',
                   'mov, BEFORE reg (raw)' if middle_panel == 'mov_raw' else 'mov, AFTER reg',
                   'overlay: red=ref, green=reg mov']
    if iou_data is not None:
        col_titles.append('IOU histogram (match diagnostics)')

    for i in range(n_pairs):
        ref_n, mov_raw_n, mov_reg_n, overlay = pairs_data[i]
        mid_n = mov_raw_n if middle_panel == 'mov_raw' else mov_reg_n
        row_axes = axes[i]
        ax_ref, ax_mid, ax_ov = row_axes[0], row_axes[1], row_axes[2]
        ax_iou = row_axes[3] if iou_data is not None else None

        ax_ref.imshow(ref_n, cmap='gray')
        ax_mid.imshow(mid_n, cmap='gray')
        ax_ov.imshow(overlay)

        row_flagged = flagged[i]
        color = 'red' if row_flagged else 'black'
        weight = 'bold' if row_flagged else 'normal'

        row_label = (f'{i}→{i+1}\n{labels[i]}\n→{labels[i+1]}\n'
                     f'SSIM={scores[i]:.3f}\nz={z[i]:.1f}' + ('\nLOW_ALIGNMENT' if row_flagged else ''))
        if phase_corr_info and i in phase_corr_info:
            _, _, ssim_pc, likely_capture_range = phase_corr_info[i]
            if likely_capture_range:
                row_label += f'\nphase-corr={ssim_pc:.3f}\nCAPTURE-RANGE?'
        if iou_data is not None and iou_data[i] is not None:
            _, _, rate = iou_data[i]
            row_label += f'\nIOU match={rate:.0%}'
        ax_ref.set_ylabel(row_label, rotation=0, ha='right', va='center', fontsize=7.5,
                           color=color, fontweight=weight, labelpad=8)

        if ax_iou is not None:
            if iou_data[i] is not None:
                iou_arr, thr, rate = iou_data[i]
                ax_iou.hist(iou_arr, bins=30, range=(0, 1), color='steelblue', edgecolor='none')
                ax_iou.axvline(thr, color='red', linestyle='--', linewidth=1.2)
                ax_iou.set_xlim(0, 1)
                ax_iou.tick_params(labelsize=6)
            else:
                ax_iou.text(0.5, 0.5, 'no IOU data\n(run track2p first)', ha='center', va='center',
                            fontsize=7.5, color='gray', transform=ax_iou.transAxes)
                ax_iou.set_xticks([])
                ax_iou.set_yticks([])

        for ax in (ax_ref, ax_mid, ax_ov):
            ax.set_xticks([])
            ax.set_yticks([])
            if row_flagged:
                for spine in ax.spines.values():
                    spine.set_edgecolor('red')
                    spine.set_linewidth(3)
        if ax_iou is not None and row_flagged:
            for spine in ax_iou.spines.values():
                spine.set_edgecolor('red')
                spine.set_linewidth(3)

        if i == 0:
            ax_ref.set_title(col_titles[0], fontsize=9)
            ax_mid.set_title(col_titles[1], fontsize=9)
            ax_ov.set_title(col_titles[2], fontsize=9)
            if ax_iou is not None:
                ax_iou.set_title(col_titles[3], fontsize=9)

    n_flagged = int(np.sum(flagged))
    fig.suptitle(f'registration_quality_scan.py {save_path} -- plane {plane} -- {n_pairs} pair(s), '
                 f'{n_flagged} flagged (red) at |z|>={z_thresh} or SSIM<={ssim_floor}', fontsize=11, y=1.0)
    plt.tight_layout(rect=[0, 0, 1, 0.99])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    return out_path


def main(argv=None):
    """argv=None (default) parses sys.argv, same as always for direct CLI use.
    Pass an explicit list to call this programmatically -- e.g. screen_and_scan.py
    invokes this in-process instead of shelling out."""
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing track_ops.npy')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--z-thresh', type=float, default=2.0,
                         help='robust z-score magnitude to flag a pair\'s SSIM as an outlier (default 2.0)')
    parser.add_argument('--ssim-floor', type=float, default=0.3,
                         help='absolute SSIM floor -- flags a pair regardless of z-score (default 0.3). Guards '
                              'against outlier masking on a heavily contaminated list, where several bad sessions '
                              'inflate the median/MAD enough that even a genuinely bad pair no longer looks like a '
                              'z-score outlier. See module docstring for how 0.3 was chosen.')
    parser.add_argument('--no-grid', action='store_true',
                         help='skip building the grid PNG, just print the table (faster on a long session list '
                              'if you only want the numbers)')
    parser.add_argument('--no-phase-corr-check', action='store_true',
                         help='skip the phase-correlation capture-range follow-up check on flagged pairs '
                              '(see module docstring)')
    parser.add_argument('--middle-panel', choices=['mov_raw', 'mov_reg'], default='mov_raw',
                         help='which mov image to show in the grid\'s middle column -- raw (BEFORE registration, '
                              'default) or registered (AFTER). See module docstring for the trade-off; the overlay '
                              'column already shows the after-reg image via its green channel.')
    parser.add_argument('--grid-out', default=None,
                         help='output PNG path for the grid (default: <save_path>/diagnostics/registration_quality_grid.png)')
    parser.add_argument('--panel-size', type=float, default=3.2, help='inches per panel (default 3.2)')
    parser.add_argument('--dpi', type=int, default=100, help='grid PNG dpi (default 100)')
    args = parser.parse_args(argv)

    track_ops = DefaultTrackOps()
    track_ops_dict = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    track_ops.from_dict(track_ops_dict)

    # NOTE: track_ops.all_ds_path here is whatever order was saved in this run's
    # track_ops.npy, which is only chronological if something upstream (run_gap_tolerant.py,
    # run_exclude_session.py) already sorted it before running -- this script didn't check on
    # its own, so a misordered save could silently produce grid rows/table pairs out of date
    # order. load_all_ds_path() + ensure_chronological_order() match the same guard those
    # launcher scripts apply before registering anything.
    all_ds_path = ensure_chronological_order(load_all_ds_path(args.save_path))
    track_ops.all_ds_path = all_ds_path
    n_sessions = len(all_ds_path)
    labels = [os.path.basename(os.path.normpath(p)) for p in all_ds_path]

    # IOU diagnostics are a separate, downstream signal from this script's own SSIM
    # registration -- only available if a track2p run (vanilla or gap-tolerant) already
    # happened at this save_path. Same file/keys screen_sessions.py and
    # estimate_fix2_ceiling.py already read: iou_values[pair][plane] -> array,
    # thresholds[pair][plane] -> float.
    diag_path = os.path.join(args.save_path, 'match_diagnostics.npy')
    iou_values = thresholds = None
    if os.path.exists(diag_path):
        diag = np.load(diag_path, allow_pickle=True).item()
        iou_values = diag['iou_values']
        thresholds = diag['thresholds']
    else:
        print(f'(no match_diagnostics.npy at {args.save_path} -- IOU histogram column will show '
              f'"no IOU data" placeholders; run track2p at least once here to unlock it)\n')

    print(f'Registering {n_sessions - 1} consecutive pair(s), plane {args.plane} -- this runs real '
          f'elastix registration for each, same cost as a normal run\'s consecutive pass...\n')

    scores = []
    pairs_data = []  # (ref_n, mov_raw_n, mov_reg_n, overlay) per pair, only kept if grid is being built
    iou_data = [] if iou_values is not None else None  # (iou_arr, thr, rate) or None, per pair
    for i in range(n_sessions - 1):
        ref_img = _load_mean_img(all_ds_path[i], args.plane)
        mov_img = _load_mean_img(all_ds_path[i + 1], args.plane)
        mov_img_reg, _ = reg_img_elastix(ref_img, mov_img, track_ops)
        mask = signal_mask(ref_img)
        ref_n = _norm01(ref_img)
        mov_reg_n = _norm01(mov_img_reg)
        score = masked_ssim(ref_n, mov_reg_n, mask)
        scores.append(score)

        iou_str = ''
        if iou_values is not None:
            iou_arr = np.asarray(iou_values[i][args.plane])
            thr = thresholds[i][args.plane]
            if len(iou_arr) > 0:
                rate = float(np.mean(iou_arr >= thr))
                iou_data.append((iou_arr, thr, rate))
                iou_str = f'  IOU match={rate:.1%}'
            else:
                iou_data.append(None)
        print(f'  [{i + 1}/{n_sessions - 1}] {labels[i]} -> {labels[i + 1]}: SSIM={score:.3f}{iou_str}')

        if not args.no_grid:
            mov_raw_n = _norm01(mov_img)
            overlay = np.zeros((*ref_img.shape, 3))
            overlay[..., 0] = ref_n
            overlay[..., 1] = mov_reg_n
            pairs_data.append((ref_n, mov_raw_n, mov_reg_n, overlay))

    z = robust_z(scores)

    header = f'\n{"pair":>12}  {"ref":<16} {"mov":<16} {"SSIM":>7} {"z":>6}'
    if iou_data is not None:
        header += f' {"IOU match":>10}'
    header += '  flag'
    print(header)
    flagged = np.zeros(n_sessions - 1, dtype=bool)
    suspects = []
    for i in range(n_sessions - 1):
        is_z_outlier = z[i] <= -args.z_thresh
        is_below_floor = scores[i] <= args.ssim_floor
        flagged[i] = is_z_outlier or is_below_floor
        flag = ''
        if flagged[i]:
            reasons = []
            if is_z_outlier:
                reasons.append('z-score')
            if is_below_floor:
                reasons.append('abs-floor')
            flag = f'<-- LOW_ALIGNMENT ({"+".join(reasons)})'
            suspects.append((i, i + 1, labels[i], labels[i + 1], scores[i], z[i], reasons))
        row = f'  {i:>3}->{i+1:<3}  {labels[i]:<16} {labels[i+1]:<16} {scores[i]:>7.3f} {z[i]:>6.1f}'
        if iou_data is not None:
            row += f' {iou_data[i][2]:>9.1%}' if iou_data[i] is not None else f' {"n/a":>9}'
        row += f'  {flag}'
        print(row)

    print('\n' + '=' * 70)
    if suspects:
        print(f'{len(suspects)} pair(s) with anomalously low registration alignment:')
        for i, k, ref_lbl, mov_lbl, score, zscore, reasons in suspects:
            print(f'  {i}->{k} ({ref_lbl} -> {mov_lbl}): SSIM={score:.3f} (z={zscore:.1f}, {"+".join(reasons)})')
        print('\nA session showing up in TWO flagged pairs (both its neighbor transitions) is much')
        print('stronger evidence than one flagged pair alone -- cross-reference against')
        print('screen_sessions.py\'s BAD_NEIGHBOR_TRANSITIONS flag before excluding anything.')
        print('\n"abs-floor"-only flags (no z-score) are exactly what to watch for on a list with several')
        print('simultaneously bad sessions -- that\'s outlier masking suppressing the z-score, see module docstring.')
    else:
        print('No pairs flagged -- registration alignment looks consistent across the whole list.')

    phase_corr_info = {}  # i -> (row_shift, col_shift, ssim_pc, likely_capture_range)
    if suspects and not args.no_phase_corr_check:
        print('\n' + '=' * 70)
        print('Phase-correlation follow-up on flagged pair(s) (checks for a capture-range failure --')
        print('see module docstring):')
        for i, k, ref_lbl, mov_lbl, score, zscore, reasons in suspects:
            ref_img = _load_mean_img(all_ds_path[i], args.plane)
            mov_img = _load_mean_img(all_ds_path[k], args.plane)
            row_shift, col_shift = phase_correlation_shift(ref_img, mov_img)
            mov_img_pc = np.roll(np.roll(mov_img, row_shift, axis=0), col_shift, axis=1)
            mask = signal_mask(ref_img)
            ssim_pc = masked_ssim(_norm01(ref_img), _norm01(mov_img_pc), mask)
            likely_capture_range = ssim_pc > score + 0.1
            phase_corr_info[i] = (row_shift, col_shift, ssim_pc, likely_capture_range)

            shift_mag = float(np.hypot(row_shift, col_shift))
            print(f'  {i}->{k} ({ref_lbl} -> {mov_lbl}): elastix SSIM={score:.3f}  '
                  f'phase-corr SSIM={ssim_pc:.3f}  (shift row={row_shift:+d}px col={col_shift:+d}px, '
                  f'{shift_mag:.0f}px total)')
            if likely_capture_range:
                print(f'      ==> phase-corr notably better -- possible CAPTURE-RANGE failure, not '
                      f'necessarily genuine misalignment. Run debug_large_displacement.py --ref {i} --mov {k} '
                      f'for the full visual comparison before concluding this session is bad.')
        print('\nA capture-range failure means the underlying data likely aligns fine -- elastix\'s default')
        print('optimizer just could not find the shift from its starting point. That\'s a reason to dig')
        print('into registration settings (or just exclude the session, if not worth chasing), NOT the same')
        print('conclusion as a pair that phase correlation ALSO can\'t align.')

    if not args.no_grid:
        out_path = args.grid_out if args.grid_out is not None else os.path.join(
            args.save_path, 'diagnostics', 'registration_quality_grid.png')
        _build_grid(pairs_data, labels, scores, z, flagged, args.z_thresh, args.ssim_floor, args.middle_panel,
                    args.plane, out_path, args.panel_size, args.dpi, phase_corr_info, iou_data)
        print(f'\nSaved grid PNG: {os.path.abspath(out_path)}')
        print('One row per pair -- ref / mov-before-reg / overlay' +
              (' / IOU histogram' if iou_data is not None else '') +
              ' by default (--middle-panel mov_reg to swap the middle column). Flagged rows (|z| >= threshold OR '
              'SSIM <= floor) have a red label and red panel borders; rows with a likely capture-range failure '
              'additionally note it in the label.')
        if iou_data is not None:
            print('IOU histogram column: dashed red line is the Otsu/min threshold for that pair. Eyeball for '
                  'bimodality -- a clean split between a low "different cell" peak and a high "real match" peak '
                  'means the threshold can be trusted; a collapsed/unimodal histogram means it can\'t, even on a '
                  'pair whose SSIM/overlay look fine.')


if __name__ == '__main__':
    main()
