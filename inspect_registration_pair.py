"""
inspect_registration_pair.py

Visual registration-quality check for a SPECIFIC (ref, mov) session pair --
complements export_session_qc.py, which only shows each session's own raw
mean image side by side and can't reveal a registration/alignment problem
between two sessions that individually look fine (normal cell count, sharp
image) but don't line up well against each other. That's exactly the
signature BAD_NEIGHBOR_TRANSITIONS in screen_sessions.py is built to catch:
a session whose own image quality is unremarkable, but whose IOU-based
match rate to its immediate neighbors is anomalously low -- a mean-image
side-by-side comparison legitimately cannot distinguish "this session's
data is bad" from "this session's data is fine but doesn't REGISTER well
against its neighbors" (FOV shift, rotation, focal-plane drift, etc.).
This script checks the second possibility directly.

Registers mov onto ref using the SAME elastix call track2p's own pipeline
uses (track2p.register.elastix.reg_img_elastix, also used by
fix1_gap_tolerant_chain.py's gap registrations) -- not a re-implementation,
the literal same registration this session pair would get in a real run.
Produces a 4-panel figure:
  1. ref image (raw)
  2. mov image (raw, BEFORE registration)
  3. mov image (AFTER registration onto ref)
  4. red/green overlay of ref (red) vs. registered mov (green) --
     well-aligned structures appear yellow/white; misaligned structures
     show up as separated red/green fringes, which is the fast visual
     tell for a real registration failure vs. a merely-noisy image.

Usage:
    python inspect_registration_pair.py /path/to/track2p/save_path --ref 3 --mov 4
    python inspect_registration_pair.py /path/to/track2p/save_path --ref 12-02-25 --mov 12-16-25

--ref/--mov each accept EITHER a plain 0-indexed integer OR a date/substring
uniquely identifying a session folder (same substring-match convention as
EXCLUDE_MATCH in run_exclude_session.py). Prefer the date form whenever
session indices might have shifted since you last looked -- e.g. right
after an exclusion round, session N's index isn't the same list position it
used to be, and a numeric --ref/--mov silently points at whatever session
now happens to sit at that position instead of failing loudly. A date
string can't be mistaken for an index (it isn't a valid int), so there's no
ambiguity in mixing the two forms across --ref/--mov in the same call.

Reuses track_ops.npy from save_path (all_ds_path, reg_chan, transform_type,
etc.) so this registers with the exact same settings your real run used.

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
from registration_qc_utils import load_mean_img as _load_mean_img, norm01 as _norm01, signal_mask, masked_ssim


def _resolve_session(all_ds_path, spec, arg_name):
    """Resolve a --ref/--mov value to a 0-indexed session position. Accepts
    either a plain integer (treated as the index directly) or a
    date/substring matched against session folder names (same convention as
    EXCLUDE_MATCH in run_exclude_session.py) -- fails loudly if a substring
    doesn't match exactly one session, rather than silently picking the
    wrong one the way a stale numeric index would."""
    try:
        return int(spec)
    except ValueError:
        pass  # not a plain int -- fall through to substring match

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
    parser.add_argument('--ref', required=True,
                         help='reference session -- 0-indexed integer, OR a date/substring '
                              'uniquely identifying it (e.g. "12-16-25")')
    parser.add_argument('--mov', required=True,
                         help='moving session to register onto ref -- 0-indexed integer, OR a '
                              'date/substring uniquely identifying it')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--out', default=None, help='output PNG path (default: <save_path>/diagnostics/reg_check_<ref>_<mov>.png)')
    args = parser.parse_args()

    track_ops = DefaultTrackOps()
    track_ops_dict = np.load(os.path.join(args.save_path, 'track_ops.npy'), allow_pickle=True).item()
    track_ops.from_dict(track_ops_dict)
    all_ds_path = track_ops.all_ds_path

    ref_idx = _resolve_session(all_ds_path, args.ref, 'ref')
    mov_idx = _resolve_session(all_ds_path, args.mov, 'mov')

    ref_label = os.path.basename(os.path.normpath(all_ds_path[ref_idx]))
    mov_label = os.path.basename(os.path.normpath(all_ds_path[mov_idx]))
    print(f'Registering session {mov_idx} ({mov_label}) onto session {ref_idx} ({ref_label}), '
          f'plane {args.plane}, using this run\'s actual track_ops settings...')

    ref_img = _load_mean_img(all_ds_path[ref_idx], args.plane)
    mov_img = _load_mean_img(all_ds_path[mov_idx], args.plane)

    mov_img_reg, reg_params = reg_img_elastix(ref_img, mov_img, track_ops)

    # Quantitative alignment score, not just a visual impression -- SSIM (structural
    # similarity) between the normalized ref image and the normalized registered mov
    # image. 1.0 = identical; this measures IMAGE-level alignment directly, independent
    # of the downstream IOU/ROI-matching numbers screen_sessions.py already reports.
    # A single score means little in isolation -- run registration_quality_scan.py for
    # the full-list distribution of every consecutive pair to judge this number against,
    # rather than eyeballing it next to one anecdotal "control" pair.
    ref_n = _norm01(ref_img)
    mov_reg_n = _norm01(mov_img_reg)
    mask = signal_mask(ref_img)
    score = masked_ssim(ref_n, mov_reg_n, mask)
    print(f'Structural similarity (SSIM, masked to ref\'s brightest 20% of pixels -- see '
          f'registration_qc_utils.py for why): {score:.3f}')

    fig, axes = plt.subplots(1, 4, figsize=(24, 6))

    axes[0].imshow(_norm01(ref_img), cmap='gray')
    axes[0].set_title(f'ref: session {ref_idx}\n({ref_label})')

    axes[1].imshow(_norm01(mov_img), cmap='gray')
    axes[1].set_title(f'mov (raw, BEFORE reg): session {mov_idx}\n({mov_label})')

    axes[2].imshow(_norm01(mov_img_reg), cmap='gray')
    axes[2].set_title(f'mov (AFTER registration onto ref)')

    overlay = np.zeros((*ref_img.shape, 3))
    overlay[..., 0] = ref_n          # red = ref
    overlay[..., 1] = mov_reg_n      # green = registered mov
    axes[3].imshow(overlay)
    axes[3].set_title(f'overlay: red=ref, green=registered mov (SSIM={score:.3f})\n(yellow/white = well aligned,\nred/green fringes = misaligned)')

    for ax in axes:
        ax.axis('off')

    plt.tight_layout()

    out_path = args.out if args.out is not None else os.path.join(
        args.save_path, 'diagnostics', f'reg_check_{ref_idx}_{mov_idx}.png')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=150)
    print(f'\nSaved {os.path.abspath(out_path)}')
    print('Look at the overlay panel: consistent red/green fringing around cell bodies/vasculature')
    print('means registration genuinely failed for this pair (FOV shift, rotation, focal-plane drift,')
    print('etc. that this transform couldn\'t correct) -- not just noisy/dim data, which the mean-image')
    print('side-by-side view from export_session_qc.py already ruled out for you.')
    print(f'\nSSIM={score:.3f} -- compare against registration_quality_scan.py\'s full-list distribution')
    print('rather than a single anecdotal control pair to judge whether this is actually low.')


if __name__ == '__main__':
    main()
