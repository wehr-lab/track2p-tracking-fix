"""
registration_qc_utils.py

Shared helpers for inspect_registration_pair.py, registration_quality_scan.py,
and debug_large_displacement.py -- image loading, normalization, a
signal-masked SSIM score, and an FFT phase-correlation shift estimator.

WHY MASKED, NOT WHOLE-IMAGE SSIM: two-photon mean images are mostly dark
background (out-of-FOV area, space between cell bodies) with independent
frame-to-frame noise that carries no real alignment information. Whole-
image SSIM averages over that background along with the actual signal, and
can be dragged down by pure noise mismatch even between two genuinely
well-aligned images -- the practical symptom is every pair in a list
scoring in some mediocre 0.2-0.7 range with no clean separation between
fine and bad pairs, rather than the (0.85+ fine, notably lower bad) split
you'd expect if the metric were only picking up real misalignment. Masking
to the ref image's own brightest pixels (signal_percentile, default 80th)
before averaging keeps the score anchored to whether structures that
actually exist in the image (cell bodies, vasculature) line up, instead of
whether two independent noise fields happen to agree in the dark.
"""

import os
import numpy as np
from skimage.metrics import structural_similarity as ssim


def load_mean_img(ds_path, plane):
    ops_path = os.path.join(ds_path, 'suite2p', f'plane{plane}', 'ops.npy')
    ops = np.load(ops_path, allow_pickle=True).item()
    return ops['meanImg'].astype(np.float64)


def norm01(img):
    lo, hi = np.percentile(img, [1, 99])
    if hi <= lo:
        return np.zeros_like(img)
    return np.clip((img - lo) / (hi - lo), 0, 1)


def signal_mask(ref_img_raw, signal_percentile=80):
    """Boolean mask over ref_img_raw's brightest signal_percentile of pixels
    -- the region SSIM should actually be evaluated on."""
    thr = np.percentile(ref_img_raw, signal_percentile)
    return ref_img_raw >= thr


def masked_ssim(ref_n, mov_reg_n, mask):
    """SSIM restricted to mask (see module docstring for why). Falls back
    to whole-image SSIM if the mask is somehow empty."""
    _, ssim_map = ssim(ref_n, mov_reg_n, data_range=1.0, full=True)
    if mask.sum() == 0:
        return float(ssim_map.mean())
    return float(ssim_map[mask].mean())


def phase_correlation_shift(ref_img, mov_img):
    """Estimate the best-fit global (row_shift, col_shift) that aligns
    mov_img onto ref_img via FFT-based phase correlation. Apply via:
        aligned = np.roll(np.roll(mov_img, row_shift, axis=0), col_shift, axis=1)

    Unlike elastix's default gradient-descent optimizer, this isn't
    sensitive to displacement magnitude -- it evaluates every possible
    integer translation in a single FFT rather than iteratively searching
    from a starting guess, so it can recover a large shift a capture-range-
    limited optimizer misses. Used by debug_large_displacement.py and
    registration_quality_scan.py's flagged-pair follow-up check to
    distinguish "elastix failed to converge on an otherwise-alignable pair"
    from "these two sessions genuinely don't line up." Sign/wraparound
    convention matches preflight_registration_check.m's rigidAlignStack()
    -- cross-validated there against a synthetic-shift round-trip test.
    """
    n_rows, n_cols = ref_img.shape
    ref_fft = np.fft.fft2(ref_img)
    mov_fft = np.fft.fft2(mov_img)
    cross_power = (ref_fft * np.conj(mov_fft)) / (np.abs(ref_fft * np.conj(mov_fft)) + np.finfo(float).eps)
    corr_img = np.abs(np.fft.ifft2(cross_power))
    row_idx, col_idx = np.unravel_index(np.argmax(corr_img), (n_rows, n_cols))

    row_shift = row_idx - n_rows if row_idx > n_rows / 2 else row_idx
    col_shift = col_idx - n_cols if col_idx > n_cols / 2 else col_idx
    return int(row_shift), int(col_shift)
