"""
debug_large_displacement.py

Tests a specific hypothesis about a BAD_NEIGHBOR_TRANSITIONS pair:
that the registration failed because the true displacement between ref
and mov exceeds elastix's default optimizer's "capture range," not because
the underlying image data doesn't actually line up.

WHY THIS CAN HAPPEN: track2p's registration (reg_img_elastix, via
inspect_registration_pair.py's SAME call) uses a gradient-descent-based
optimizer (elastix's default AdaptiveStochasticGradientDescent). That kind
of optimizer needs the true shift to fall within its capture range at each
level of the multi-resolution pyramid, or it stalls near the identity
transform or converges to the wrong local minimum -- even when the
underlying image content would align just fine given a good starting
point (FOV recenter, scope bump, refocus between two otherwise-healthy
sessions). Phase correlation (this script's cross-check) doesn't have that
problem: it evaluates every possible integer translation in one FFT,
independent of magnitude, so it can recover a large shift elastix's
gradient descent missed.

This script registers the SAME (ref, mov) pair THREE ways and compares them
side by side:
  1. raw mov, no correction at all
  2. mov registered via track2p's actual reg_img_elastix() call -- the
     literal same registration this pair gets in a real run (same as
     inspect_registration_pair.py)
  3. mov shifted via FFT-based phase correlation -- NOT elastix, just a
     coarse global-translation estimate cross-validated against a
     synthetic-shift test in this project's preflight_registration_check.m (now
     shared via registration_qc_utils.phase_correlation_shift, also used by
     registration_quality_scan.py's flagged-pair follow-up check)
     (same sign convention, transplanted here)

If (3)'s SSIM is clearly higher than (2)'s, and the recovered shift is
large relative to the image size, that's strong evidence for the capture-
range hypothesis: the data aligns fine, elastix's optimizer just couldn't
find it from its default starting point. If (3) ALSO looks bad, the
problem probably isn't a simple large global shift (could be rotation,
local warping, or genuinely minimal FOV overlap) -- look at the overlay
panels directly rather than trusting either SSIM number alone.

Usage:
    python debug_large_displacement.py /path/to/track2p/save_path --ref 6 --mov 7

--ref/--mov accept either a 0-indexed integer or a date/substring, same
convention as inspect_registration_pair.py.

Reuses track_ops.npy from save_path so the elastix comparison uses this
run's actual settings.
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
from registration_qc_utils import (load_mean_img as _load_mean_img, norm01 as _norm01, signal_mask,
                                    masked_ssim, phase_correlation_shift)


def _resolve_session(all_ds_path, spec, arg_name):
    """Same convention as inspect_registration_pair.py's _resolve_session()."""
    try:
        return int(spec)
    except ValueError:
        pass

    matches = [i for i, p in enumerate(all_ds_path) if spec in os.path.basename(os.path.normpath(p))]
    if len(matches) != 1:
        raise ValueError(
            f"--{arg_name}='{spec}' matched {len(matches)} session(s) by substring, expected exactly 1: "
            f"{[os.path.basename(os.path.normpath(all_ds_path[i])) for i in matches]}\n"
            f"Use a more specific date/substring, or the numeric 0-indexed position instead."
        )
    return matches[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('save_path', help='track2p save_path containing track_ops.npy')
    parser.add_argument('--ref', required=True, help='reference session -- 0-indexed integer or date/substring')
    parser.add_argument('--mov', required=True, help='moving session -- 0-indexed integer or date/substring')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--out', default=None,
                         help='output PNG path (default: <save_path>/diagnostics/large_displacement_<ref>_<mov>.png)')
    args = parser.parse_args()

    track_ops = DefaultTrackOps()
    track_ops_dict = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    track_ops.from_dict(track_ops_dict)
    all_ds_path = track_ops.all_ds_path

    ref_idx = _resolve_session(all_ds_path, args.ref, 'ref')
    mov_idx = _resolve_session(all_ds_path, args.mov, 'mov')
    ref_label = os.path.basename(os.path.normpath(all_ds_path[ref_idx]))
    mov_label = os.path.basename(os.path.normpath(all_ds_path[mov_idx]))

    print(f'Comparing elastix vs. phase-correlation for session {mov_idx} ({mov_label}) -> '
          f'session {ref_idx} ({ref_label}), plane {args.plane}...')

    ref_img = _load_mean_img(all_ds_path[ref_idx], args.plane)
    mov_img = _load_mean_img(all_ds_path[mov_idx], args.plane)
    n_rows, n_cols = ref_img.shape

    # 1. track2p's actual registration -- same call inspect_registration_pair.py uses
    mov_img_elastix, _ = reg_img_elastix(ref_img, mov_img, track_ops)

    # 2. phase correlation -- coarse global translation, not gradient-descent-based
    row_shift, col_shift = phase_correlation_shift(ref_img, mov_img)
    mov_img_pc = np.roll(np.roll(mov_img, row_shift, axis=0), col_shift, axis=1)

    shift_mag = float(np.hypot(row_shift, col_shift))
    print(f'\nPhase-correlation shift estimate: row={row_shift:+d}px, col={col_shift:+d}px '
          f'({100 * abs(row_shift) / n_rows:.0f}% of height, {100 * abs(col_shift) / n_cols:.0f}% of width) '
          f'-- magnitude {shift_mag:.0f}px')

    ref_n = _norm01(ref_img)
    mask = signal_mask(ref_img)
    ssim_raw = masked_ssim(ref_n, _norm01(mov_img), mask)
    ssim_elastix = masked_ssim(ref_n, _norm01(mov_img_elastix), mask)
    ssim_pc = masked_ssim(ref_n, _norm01(mov_img_pc), mask)

    print(f'\nMasked SSIM (ref\'s brightest 20% of pixels):')
    print(f'  raw (no correction):        {ssim_raw:.3f}')
    print(f'  track2p elastix result:     {ssim_elastix:.3f}')
    print(f'  phase-correlation shift:    {ssim_pc:.3f}')

    if ssim_pc > ssim_elastix + 0.1 and ssim_pc > ssim_raw + 0.1:
        print('\n==> Phase correlation found a substantially better alignment than elastix did.')
        print('    Consistent with the capture-range hypothesis: the data likely aligns fine, but')
        print(f'    the {shift_mag:.0f}px shift needed exceeds what elastix\'s default optimizer could')
        print('    find from its starting point. Check the overlay panels below to confirm visually --')
        print('    if the phase-correlation overlay looks genuinely well-aligned (yellow/white, not')
        print('    fringed), that confirms it. Fix options: give reg_img_elastix() a better starting')
        print('    point (AutomaticTransformInitialization / CenterOfGravity), more pyramid resolutions,')
        print('    or a larger optimizer step length -- all changes to track2p\'s own registration call,')
        print('    not something in this repo -- or just exclude this session if that\'s not worth it.')
    elif ssim_pc <= max(ssim_elastix, ssim_raw) + 0.05:
        print('\n==> Phase correlation did NOT do meaningfully better than elastix (or raw).')
        print('    A simple large global shift probably is not the whole story here -- could be')
        print('    rotation, local/nonrigid warping, focal-plane drift, or genuinely minimal FOV')
        print('    overlap between these two sessions. Look at the overlay panels directly rather')
        print('    than concluding from SSIM alone.')
    else:
        print('\n==> Phase correlation did somewhat better than elastix, but not dramatically --')
        print('    inconclusive from SSIM alone. Look at the overlay panels directly.')

    fig, axes = plt.subplots(2, 4, figsize=(24, 12))

    axes[0, 0].imshow(ref_n, cmap='gray')
    axes[0, 0].set_title(f'ref: session {ref_idx}\n({ref_label})')
    axes[1, 0].axis('off')

    mov_raw_n = _norm01(mov_img)
    axes[0, 1].imshow(mov_raw_n, cmap='gray')
    axes[0, 1].set_title(f'mov (raw): session {mov_idx}\n({mov_label})')
    overlay_raw = np.zeros((*ref_img.shape, 3))
    overlay_raw[..., 0] = ref_n
    overlay_raw[..., 1] = mov_raw_n
    axes[1, 1].imshow(overlay_raw)
    axes[1, 1].set_title(f'overlay: raw (SSIM={ssim_raw:.3f})')

    mov_elastix_n = _norm01(mov_img_elastix)
    axes[0, 2].imshow(mov_elastix_n, cmap='gray')
    axes[0, 2].set_title('mov (track2p elastix)')
    overlay_elastix = np.zeros((*ref_img.shape, 3))
    overlay_elastix[..., 0] = ref_n
    overlay_elastix[..., 1] = mov_elastix_n
    axes[1, 2].imshow(overlay_elastix)
    axes[1, 2].set_title(f'overlay: elastix (SSIM={ssim_elastix:.3f})')

    mov_pc_n = _norm01(mov_img_pc)
    axes[0, 3].imshow(mov_pc_n, cmap='gray')
    axes[0, 3].set_title(f'mov (phase-corr shift)\nrow={row_shift:+d}px, col={col_shift:+d}px')
    overlay_pc = np.zeros((*ref_img.shape, 3))
    overlay_pc[..., 0] = ref_n
    overlay_pc[..., 1] = mov_pc_n
    axes[1, 3].imshow(overlay_pc)
    axes[1, 3].set_title(f'overlay: phase-corr (SSIM={ssim_pc:.3f})')

    for ax in axes.flat:
        ax.axis('off')

    plt.tight_layout()

    out_path = args.out if args.out is not None else os.path.join(
        args.save_path, 'diagnostics', f'large_displacement_{ref_idx}_{mov_idx}.png')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f'\nSaved {os.path.abspath(out_path)}')


if __name__ == '__main__':
    main()
