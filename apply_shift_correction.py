"""
apply_shift_correction.py

Pre-corrects a session's suite2p output for a large, already-known FOV
offset (row_shift, col_shift) -- e.g. one confirmed via
debug_large_displacement.py / registration_quality_scan.py's phase-
correlation follow-up -- BEFORE handing it to track2p, so track2p's own
elastix call only has to correct whatever small residual remains (well
within its normal capture range) instead of failing outright on the full
displacement.

WHY NOT JUST TUNE ELASTIX INSTEAD: reg_img_elastix.py's actual source
isn't available in this project to patch its optimizer/capture-range
settings precisely (see export_elastix_params.py's docstring for the same
caveat). Pre-shifting the data ahead of time sidesteps that entirely --
it's a plain, deterministic pixel translation, no elastix internals
touched, and the shift itself is already independently confirmed correct
(recovered by phase correlation AND visually confirmed as a well-aligned
overlay).

WHAT GETS CORRECTED, per plane (confirmed against a real matched_suite2p
stat.npy's field names -- ypix, xpix, lam, med, footprint, mrs, mrs0,
compact, solidity, npix, npix_soma, soma_crop, overlap, radius,
aspect_ratio, npix_norm*, skew, std, neuropil_mask):

  ops.npy   -- meanImg, plus any other same-shape 2D image key found
               (meanImgE, max_proj, Vcorr, ...) -- shifted for consistency
               even though only meanImg is what this project's tools
               actually use. Uses a NON-circular shift (the edge strip
               that shift would wrap in from the opposite side is zeroed
               instead) -- unlike the quick diagnostic check in
               debug_large_displacement.py / registration_qc_utils.py,
               which circularly wraps for a fast whole-image SSIM sanity
               check. Wrapping unrelated content into a strip of the image
               that was genuinely never imaged would be actively wrong for
               data track2p is about to register against, not just
               imprecise.

  stat.npy  -- per-ROI SPATIAL fields only: ypix, xpix, med. This is
               deliberately scoped to just what track2p's IOU-based ROI
               matching actually operates on (spatial pixel masks), not
               everything in the file:
                 - lam, soma_crop, overlap are per-pixel VALUES that travel
                   with ypix/xpix unchanged (same length, same order) --
                   left alone except for being sub-selected alongside
                   ypix/xpix if some of an ROI's pixels get clipped.
                 - mrs, mrs0, compact, solidity, radius, aspect_ratio,
                   skew, std, footprint are shape/quality metrics computed
                   from the ROI's pixel pattern, which a pure translation
                   doesn't change -- left alone.
                 - npix, npix_soma are recomputed after clipping (see
                   below), since a stale count would be actively wrong if
                   any pixels were dropped.
                 - neuropil_mask is deliberately NOT touched: it's a
                   flattened-index mask over the FULL image used for
                   fluorescence neuropil subtraction (F.npy/Fneu.npy-
                   adjacent), not something track2p's spatial matching
                   references. Translating it correctly needs Ly/Lx from
                   ops.npy for the flatten convention and wasn't validated
                   here -- if some other downstream tool DOES depend on
                   neuropil_mask being accurate post-shift, this would
                   need revisiting.

  Pixels that shift outside the valid image bounds are clipped (dropped
  from that ROI's ypix/xpix/lam/soma_crop/overlap together, keeping them
  aligned) rather than wrapped -- same reasoning as the meanImg shift
  above. If an ROI loses ALL its pixels this way, the whole ROI is
  dropped from stat.npy, and the matching row is dropped from iscell.npy
  too (kept index-aligned with stat.npy).

  iscell.npy -- filtered to match any ROIs dropped from stat.npy.

  F.npy, Fneu.npy, spks.npy, redcell.npy (per-ROI files, if present) --
  filtered by the same keep_mask as iscell.npy, so they stay index-aligned
  with the corrected stat.npy/iscell.npy. (Earlier versions of this script
  copied these unchanged on the theory that track2p's matching only reads
  stat.npy/iscell.npy/ops.npy's meanImg -- true for the matching step
  itself, but track2p's save_in_s2p_format() does F[iscell[:,0]==1,:],
  which crashes with a shape mismatch if F.npy wasn't dropped in lockstep
  with iscell.npy. Confirmed on real data.) Everything else in the plane
  folder is copied unchanged.

Usage (auto-computed shift -- the normal path):
    python apply_shift_correction.py /path/to/mov_session_dir --ref /path/to/ref_session_dir \
        --plane 0 --out /path/to/mov_session_dir_shift_corrected

Computes the shift itself via the SAME phase-correlation function
debug_large_displacement.py / registration_quality_scan.py's flagged-pair
follow-up already use (registration_qc_utils.phase_correlation_shift) --
no copy-pasting numbers from a separate diagnostic run. Prints the
recovered shift and a before/after masked-SSIM sanity check, and REFUSES
to write output if phase correlation doesn't look like it actually helped
(masked SSIM doesn't improve by at least --min-ssim-gain, default 0.1) --
pass --force to apply anyway if you've independently confirmed it's fine
(e.g. visually, via debug_large_displacement.py) despite a marginal
number.

Usage (manual override -- skip auto-detection, use an already-known shift):
    python apply_shift_correction.py /path/to/mov_session_dir --row-shift 2 --col-shift -64 \
        --plane 0 --out /path/to/mov_session_dir_shift_corrected

Sign convention: aligned_meanImg ~= shift(mov_meanImg, row_shift, col_shift);
roi_in_ref_frame = roi_in_mov_frame's ypix/xpix + (row_shift, col_shift).

After running, point ALL_DS_PATH at the corrected copy in place of the
original session for your next run_gap_tolerant.py run -- leaves the
original session folder completely untouched.
"""

import os
import shutil
import time
import argparse
import numpy as np

from registration_qc_utils import (load_mean_img as _load_mean_img, norm01 as _norm01, signal_mask,
                                    masked_ssim, phase_correlation_shift)


def shift_image(img, row_shift, col_shift, fill_value=0.0):
    """Translate img by (row_shift, col_shift), zero-filling the edge strip
    that a circular roll would otherwise wrap in from the opposite side --
    that strip was never actually imaged in this frame, so leaving it
    filled is more correct than wrapping in unrelated content."""
    shifted = np.roll(np.roll(img, row_shift, axis=0), col_shift, axis=1)
    if row_shift > 0:
        shifted[:row_shift, :] = fill_value
    elif row_shift < 0:
        shifted[row_shift:, :] = fill_value
    if col_shift > 0:
        shifted[:, :col_shift] = fill_value
    elif col_shift < 0:
        shifted[:, col_shift:] = fill_value
    return shifted


def shift_stat(stat, row_shift, col_shift, n_rows, n_cols):
    """Returns (new_stat, n_dropped, n_clipped) -- new_stat is a list of
    corrected ROI dicts (ROIs that lost all their pixels are omitted), plus
    a boolean keep-mask (same length as the input stat) for filtering
    iscell.npy/other per-ROI files consistently."""
    new_stat = []
    keep_mask = np.zeros(len(stat), dtype=bool)
    n_dropped = 0
    n_clipped = 0

    for roi_idx, roi in enumerate(stat):
        roi = dict(roi)  # shallow copy -- don't mutate the original in place
        # Cast to a signed 64-bit dtype BEFORE adding the shift, explicitly --
        # ypix/xpix are commonly stored as an unsigned dtype (uint16 is a
        # common suite2p convention), and array(uint16) + negative_python_int
        # is NOT safe to assume: depending on numpy version, it either raises
        # OverflowError outright (numpy>=2.0's NEP 50 scalar-promotion rules)
        # or silently wraps around to a huge value (older numpy in some
        # code paths) -- confirmed both failure modes empirically before
        # adding this cast. Either way, an in-bounds check against a wrapped
        # or crashed value would be silently wrong, not loudly wrong.
        ypix_new = roi['ypix'].astype(np.int64) + row_shift
        xpix_new = roi['xpix'].astype(np.int64) + col_shift
        in_bounds = (ypix_new >= 0) & (ypix_new < n_rows) & (xpix_new >= 0) & (xpix_new < n_cols)

        if not np.any(in_bounds):
            n_dropped += 1
            continue  # ROI lost all its pixels -- drop it (and its iscell.npy row) entirely
        elif not np.all(in_bounds):
            n_clipped += 1  # partially out of bounds -- some pixels dropped, ROI itself kept

        roi['ypix'] = ypix_new[in_bounds]
        roi['xpix'] = xpix_new[in_bounds]
        if 'lam' in roi and hasattr(roi['lam'], '__len__') and len(roi['lam']) == len(in_bounds):
            roi['lam'] = roi['lam'][in_bounds]
        if 'soma_crop' in roi and hasattr(roi['soma_crop'], '__len__') and len(roi['soma_crop']) == len(in_bounds):
            roi['soma_crop'] = roi['soma_crop'][in_bounds]
        if 'overlap' in roi and hasattr(roi['overlap'], '__len__') and len(roi['overlap']) == len(in_bounds):
            roi['overlap'] = roi['overlap'][in_bounds]

        if 'med' in roi:
            roi['med'] = [roi['med'][0] + row_shift, roi['med'][1] + col_shift]

        roi['npix'] = int(len(roi['ypix']))
        if 'soma_crop' in roi:
            roi['npix_soma'] = int(np.sum(roi['soma_crop']))

        new_stat.append(roi)
        keep_mask[roi_idx] = True

    return new_stat, keep_mask, n_dropped, n_clipped


def main():
    t_start = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument('session_dir', help='mov session folder to correct (contains suite2p/plane{j}/)')
    parser.add_argument('--ref', default=None,
                         help='reference session dir -- if given, the shift is computed automatically via '
                              'phase correlation against this session\'s meanImg (the normal path). Mutually '
                              'exclusive with --row-shift/--col-shift.')
    parser.add_argument('--row-shift', type=int, default=None,
                         help='manual override: row shift in pixels (skips auto-detection). Same sign '
                              'convention as registration_qc_utils.phase_correlation_shift(). Requires '
                              '--col-shift too; do not combine with --ref.')
    parser.add_argument('--col-shift', type=int, default=None, help='manual override: col shift in pixels')
    parser.add_argument('--min-ssim-gain', type=float, default=0.1,
                         help='auto-detect mode only: minimum masked-SSIM improvement (phase-corr shift vs. '
                              'raw, no shift) required to proceed -- refuses to write output below this, since '
                              'that means phase correlation likely did not find a real fix. Default 0.1, same '
                              'threshold registration_quality_scan.py\'s flagged-pair follow-up uses.')
    parser.add_argument('--min-roi-keep-frac', type=float, default=0.5,
                         help='minimum fraction of ROIs that must survive the shift (i.e. stay in-bounds) to '
                              'proceed -- applies in BOTH auto-detect and manual mode. A real translation-only '
                              'capture-range fix should only lose ROIs in a narrow edge band, so a big drop is '
                              'a strong signal something is wrong (wrong shift, wrong image dims, a dtype bug) '
                              'rather than a real edge effect. Default 0.5 (refuse if more than half is lost).')
    parser.add_argument('--force', action='store_true',
                         help='write output even if the --min-ssim-gain or --min-roi-keep-frac check fails')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--out', required=True, help='output session folder (created fresh; original untouched)')
    args = parser.parse_args()

    manual = args.row_shift is not None or args.col_shift is not None
    if args.ref is not None and manual:
        raise ValueError('Pass either --ref (auto-detect) or --row-shift/--col-shift (manual), not both.')
    if manual and (args.row_shift is None or args.col_shift is None):
        raise ValueError('--row-shift and --col-shift must be given together.')
    if args.ref is None and not manual:
        raise ValueError('Pass either --ref /path/to/ref_session_dir (auto-detect) or '
                          '--row-shift N --col-shift N (manual).')

    for label, p in [('session_dir', args.session_dir), ('--ref', args.ref)]:
        if p is not None and 'matched_suite2p' in os.path.normpath(p).split(os.sep):
            msg = (f'{label}={p!r} runs through a "matched_suite2p" folder -- that\'s track2p\'s own '
                   f'regenerated output from a PRIOR run (only cells that survived the ENTIRE chain in that '
                   f'run, not the session\'s real ROI set), not raw suite2p data. You almost certainly want '
                   f'the original raw session directory instead (same convention find_session_dirs() expects).')
            if not args.force:
                raise SystemExit(f'Refusing to proceed: {msg} Pass --force if this is really what you meant.')
            print(f'WARNING (--force): {msg}')

    plane_dir = os.path.join(args.session_dir, 'suite2p', f'plane{args.plane}')
    if not os.path.isdir(plane_dir):
        raise FileNotFoundError(f'No such directory: {plane_dir}')

    if args.ref is not None:
        ref_img = _load_mean_img(args.ref, args.plane)
        mov_img = _load_mean_img(args.session_dir, args.plane)
        row_shift, col_shift = phase_correlation_shift(ref_img, mov_img)
        mov_img_shifted = shift_image(mov_img, row_shift, col_shift)
        mask = signal_mask(ref_img)
        ref_n = _norm01(ref_img)
        ssim_before = masked_ssim(ref_n, _norm01(mov_img), mask)
        ssim_after = masked_ssim(ref_n, _norm01(mov_img_shifted), mask)
        gain = ssim_after - ssim_before
        print(f'Auto-detected shift via phase correlation: row={row_shift:+d}px, col={col_shift:+d}px')
        print(f'Masked SSIM: before={ssim_before:.3f}  after={ssim_after:.3f}  (gain={gain:+.3f})')
        if gain < args.min_ssim_gain and not args.force:
            raise SystemExit(
                f'Refusing to write output: SSIM gain ({gain:+.3f}) is below --min-ssim-gain ({args.min_ssim_gain}). '
                f'This usually means phase correlation did not find a real fix for this pair -- the problem may '
                f'not be a simple large translation (rotation, local warping, minimal FOV overlap). Check the '
                f'overlay via debug_large_displacement.py before trusting this shift, or pass --force if you\'ve '
                f'already confirmed it visually despite the marginal number.'
            )
    else:
        row_shift, col_shift = args.row_shift, args.col_shift
        print(f'Using manually-specified shift: row={row_shift:+d}px, col={col_shift:+d}px')

    out_plane_dir = os.path.join(args.out, 'suite2p', f'plane{args.plane}')
    os.makedirs(out_plane_dir, exist_ok=True)

    # ---- ops.npy: shift meanImg + any other same-shape 2D image key ----
    ops = np.load(os.path.join(plane_dir, 'ops.npy'), allow_pickle=True).item()
    ref_shape = np.asarray(ops['meanImg']).shape
    shifted_keys = []
    for key, val in ops.items():
        arr = np.asarray(val) if isinstance(val, np.ndarray) else None
        if arr is not None and arr.shape == ref_shape and arr.ndim == 2:
            ops[key] = shift_image(arr, row_shift, col_shift)
            shifted_keys.append(key)
    print(f'ops.npy: shifted image key(s) {shifted_keys} by (row={row_shift:+d}, col={col_shift:+d})')
    np.save(os.path.join(out_plane_dir, 'ops.npy'), ops, allow_pickle=True)

    # ---- stat.npy (+ iscell.npy, kept index-aligned) ----
    stat = np.load(os.path.join(plane_dir, 'stat.npy'), allow_pickle=True)
    n_rows, n_cols = ref_shape
    new_stat, keep_mask, n_dropped, n_clipped = shift_stat(stat, row_shift, col_shift, n_rows, n_cols)
    keep_frac = len(new_stat) / len(stat) if len(stat) > 0 else 1.0
    print(f'stat.npy: {len(stat)} ROI(s) -> {len(new_stat)} kept ({keep_frac:.1%}), {n_dropped} dropped entirely '
          f'(shifted fully out of bounds), {n_clipped} had some pixels clipped')
    if keep_frac < args.min_roi_keep_frac and not args.force:
        raise SystemExit(
            f'Refusing to write output: only {keep_frac:.1%} of ROIs survived the shift, below '
            f'--min-roi-keep-frac ({args.min_roi_keep_frac:.0%}). A real translation-only fix should only lose '
            f'ROIs in a narrow edge band -- this magnitude of loss usually means the shift, image dimensions, '
            f'or ypix/xpix dtype handling is wrong, not a real edge effect. Nothing was written. Check the '
            f'shift value and image dimensions before retrying, or pass --force if you\'ve independently '
            f'confirmed this is expected.'
        )
    np.save(os.path.join(out_plane_dir, 'stat.npy'), np.array(new_stat, dtype=object), allow_pickle=True)

    iscell_path = os.path.join(plane_dir, 'iscell.npy')
    if os.path.exists(iscell_path):
        iscell = np.load(iscell_path, allow_pickle=True)
        np.save(os.path.join(out_plane_dir, 'iscell.npy'), iscell[keep_mask], allow_pickle=True)
        print(f'iscell.npy: filtered to match ({keep_mask.sum()} row(s) kept)')

    # ---- per-ROI files: filter by keep_mask so they stay index-aligned ----
    # (F/Fneu/spks/redcell are all indexed by ROI order, same as stat.npy/
    # iscell.npy -- track2p's save_in_s2p_format() does F[iscell[:,0]==1,:],
    # which requires F.npy's row count to match iscell.npy's exactly, so
    # these can't just be copied unchanged if any ROI was dropped.)
    per_roi_files = {'F.npy', 'Fneu.npy', 'spks.npy', 'redcell.npy'}
    filtered_per_roi = []
    skipped_per_roi_mismatch = []
    skipped_dirs = []
    for fname in os.listdir(plane_dir):
        if fname in ('ops.npy', 'stat.npy', 'iscell.npy'):
            continue
        src = os.path.join(plane_dir, fname)
        if os.path.isdir(src):
            # e.g. suite2p's reg_tif/ (registered movie frames) -- not a
            # single file copyfile() can handle, and not needed for
            # track2p's matching (which only touches ops.npy's meanImg +
            # stat.npy/iscell.npy) anyway, so skipped rather than an
            # expensive recursive copytree of movie data nothing here uses.
            skipped_dirs.append(fname)
            continue
        if fname in per_roi_files:
            arr = np.load(src, allow_pickle=True)
            if arr.shape[0] == len(keep_mask):
                np.save(os.path.join(out_plane_dir, fname), arr[keep_mask], allow_pickle=True)
                filtered_per_roi.append(fname)
                continue
            else:
                print(f'WARNING: {fname} has {arr.shape[0]} row(s), expected {len(keep_mask)} (stat.npy\'s '
                      f'original length) -- cannot filter by keep_mask, copying unchanged instead. This file '
                      f'will be misaligned with the corrected stat.npy/iscell.npy if any ROI was dropped.')
                skipped_per_roi_mismatch.append(fname)
        # copyfile(), not copy2() -- copy2() also tries to replicate metadata
        # (mtime, macOS chflags, ...) via copystat(), which can raise
        # PermissionError when the source and destination are on different
        # volume types (e.g. a network-mounted source, local disk dest) --
        # confirmed on real data. File CONTENT is all that's needed here.
        shutil.copyfile(src, os.path.join(out_plane_dir, fname))

    if skipped_dirs:
        print(f'Skipped subdirector(ies) {skipped_dirs} (e.g. registered-movie frames) -- not needed for '
              f'track2p\'s matching, not copied.')

    if filtered_per_roi:
        print(f'Filtered to match dropped ROIs (kept {keep_mask.sum()} of {len(keep_mask)} row(s)): '
              f'{filtered_per_roi}')

    if n_dropped > 0 and skipped_per_roi_mismatch:
        print(f'\nWARNING: {n_dropped} ROI(s) were dropped from stat.npy/iscell.npy, but {skipped_per_roi_mismatch} '
              f'could not be filtered (unexpected row count) and were copied UNCHANGED -- they are no longer '
              f'index-aligned with the corrected stat.npy/iscell.npy.')

    print(f'\nWrote corrected session to {os.path.abspath(args.out)}')
    print('Point ALL_DS_PATH at this folder in place of the original for your next run_gap_tolerant.py run.')
    print(f'Elapsed time: {time.time() - t_start:.1f}s')


if __name__ == '__main__':
    main()
