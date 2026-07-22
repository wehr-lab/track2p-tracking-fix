"""
fix1_gap_tolerant_chain.py

Fix #1: gap-tolerant chaining. Structural fix for track2p's chaining rule
(track2p/match/loop.py: get_all_pl_match_mat), which only ever tests session
i against session i+1. The moment one transition's IOU falls below that
pair's local Otsu threshold, the cell's track is PERMANENTLY truncated --
even if the same cell is trivially re-identifiable against session i+2 or
i+3. This is the confirmed mechanism behind both the acute single-session
dropout and the p^(N-1) combinatorial decay at 12-13 sessions (see
track2p_investigation_notes.md).

Mechanism: on a failed i -> i+1 transition, before giving up, try matching
session i directly against i+2, i+3, ... up to max_gap. "Directly" means a
fresh elastix registration between that specific non-adjacent pair, not a
composed transform -- composing consecutive bspline transforms isn't
generally valid, direct registration is simple and robust.

NOT a free postprocessing pass like fix3_partial_tracks.py. Extra
non-adjacent registrations cost real elastix compute. To keep that bounded,
gap registrations are computed LAZILY and CACHED by default: a given
(session_i, session_k) pair is only ever registered once, and only when
some ROI's chain actually needs it (i.e. its i -> i+1 transition already
failed). Transitions that succeed at gap=1 (the common case) never trigger
any extra work, so cost scales with how much dropout there actually is, not
with max_gap * n_sessions.

Every gap registration (lazy or parallel-precomputed, see below) is
persisted to a checkpoint file on disk as soon as it's computed -- gap
registrations are the expensive, crash-prone part of this whole approach
(real elastix calls, potentially hundreds of them), so losing that work to
a mid-run crash (e.g. native heap corruption in the elastix bindings,
observed in practice on a large/noisy session list) is exactly what this
guards against. Just rerun the same launcher script pointed at the same
save_path afterward -- already-cached pairs are skipped automatically.

OPTIONAL PARALLEL MODE: precompute_gap_pairs_parallel() computes the full
BOUNDED universe of possible gap pairs (every (i, i+gap) for gap in
2..max_gap) up front, dispatched across worker processes via
concurrent.futures.ProcessPoolExecutor -- Python's closest equivalent to
MATLAB's parfor. This trades the lazy approach's "only pay for what's
actually needed" property for "pay for the whole bounded universe, but in
parallel" -- worth it whenever wall-clock time matters more than total
elastix-call count, e.g. on a multi-core machine or HPC node. See that
function's docstring for two real gotchas (ITK's own internal
multithreading needing to be capped to avoid oversubscription, and macOS
spawn semantics requiring the CALLING script to guard its top-level code
behind `if __name__ == '__main__':`) before turning this on.

This module is NOT a patch to your track2p install -- it's an alternative
entry point that reuses track2p's own registration/matching primitives
unmodified (imported directly from the installed package) and only replaces
the final chaining step. Output file formats/locations are unchanged, so
downstream tools (including fix3_partial_tracks.py) work on the result as-is.

Usage (in place of track2p.t2p.run_t2p):

    from track2p.ops.default import DefaultTrackOps
    from fix1_gap_tolerant_chain import run_t2p_gap_tolerant

    track_ops = DefaultTrackOps()
    track_ops.all_ds_path = [...]   # same config as a normal track2p run
    ...
    run_t2p_gap_tolerant(track_ops, max_gap=3)

Start with max_gap=2 or 3 and check the printed per-plane tracked counts
(gap=1 vs. final) before going higher -- each extra unit of max_gap can
trigger up to one more elastix registration per plane per still-broken
chain, so runtime grows with how bad the dropout is, which is exactly the
9-session run this is meant for.
"""

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
from skimage.filters import threshold_otsu, threshold_minimum
from scipy.optimize import linear_sum_assignment

from track2p.io.s2p_loaders import (
    load_all_imgs, check_nplanes, load_all_ds_stat_iscell,
    load_all_ds_mean_img, load_all_ds_centroids,
)
from track2p.io.savers import npy_to_s2p, save_track_ops, save_all_pl_match_mat, save_match_diagnostics
from track2p.register.loop import run_reg_loop, reg_all_ds_all_roi
from track2p.register.elastix import reg_img_elastix, itk_reg_all_roi
from track2p.register.utils import get_all_ds_img_for_reg, get_all_ref_nonref_inters
from track2p.match.utils import get_cost_mat, get_iou, init_all_pl_match_mat
from track2p.match.loop import get_all_ds_assign
from track2p.plot.progress import plot_all_planes
from track2p.plot.output import (
    plot_reg_img_output, plot_thr_met_hist, plot_n_matched_roi,
    plot_roi_reg_output, plot_roi_match_multiplane, plot_allroi_match_multiplane,
)


def _raw_roi_array(all_ds_all_roi_array_ref, all_ds_all_roi_array_mov, s, plane_j, n_pairs):
    """Session s's ROI array in its own (unregistered) coordinate frame.

    Every session appears as 'ref' of pair s (for s in 0..n_pairs-1) or as
    'mov' of pair s-1 (for s in 1..n_pairs) in the consecutive-pair loop
    that reg_all_ds_all_roi already ran -- so the raw array for any session
    is already sitting in one of those two lists; no need to reload/rebuild
    it from stat.npy.
    """
    if s <= n_pairs - 1:
        return all_ds_all_roi_array_ref[s][plane_j]
    return all_ds_all_roi_array_mov[s - 1][plane_j]


def _assign_pair(roi_ref_raw, roi_mov_raw, ref_img, mov_img, track_ops):
    """Register roi_mov_raw's session onto roi_ref_raw's session directly,
    then compute the thresholded IOU assignment. Mirrors the per-(plane,pair)
    inner loop of track2p.match.loop.get_all_ds_assign exactly, generalized
    to an arbitrary (possibly non-adjacent) pair."""
    _, reg_params = reg_img_elastix(ref_img, mov_img, track_ops)
    roi_mov_reg = itk_reg_all_roi(roi_mov_raw, reg_params)

    cost_mat, all_inds_ref_filt, all_inds_reg_filt = get_cost_mat(roi_ref_raw, roi_mov_reg, track_ops)
    ref_ind_filt, reg_ind_filt = linear_sum_assignment(cost_mat)
    ref_ind = all_inds_ref_filt[ref_ind_filt]
    reg_ind = all_inds_reg_filt[reg_ind_filt]

    thr_met = get_iou(roi_ref_raw[:, :, ref_ind], roi_mov_reg[:, :, reg_ind])
    thr_met_compute = thr_met[thr_met > 0] if track_ops.thr_remove_zeros else thr_met

    if len(thr_met_compute) < 2 or np.all(thr_met_compute == thr_met_compute[0]):
        # too few / degenerate points for Otsu-style thresholding -> no usable matches
        return np.array([], dtype=int), np.array([], dtype=int)

    if track_ops.thr_method == 'otsu':
        thr = threshold_otsu(thr_met_compute)
    elif track_ops.thr_method == 'min':
        thr = threshold_minimum(thr_met_compute)
    else:
        raise Exception('Unsupported thr_method for gap fallback')

    keep = thr_met > thr
    return ref_ind[keep], reg_ind[keep]


def _checkpoint_path(track_ops):
    """Where the gap-registration cache gets persisted, alongside this run's
    other outputs (track_ops.save_path is already resolved to its final
    per-run directory by the time this is called -- init_save_paths() runs
    before get_all_pl_match_mat_gap in run_t2p_gap_tolerant())."""
    return os.path.join(track_ops.save_path, 'gap_cache_checkpoint.npy')


def _load_checkpoint(checkpoint_path):
    if not os.path.exists(checkpoint_path):
        return {}
    cache = np.load(checkpoint_path, allow_pickle=True).item()
    print(f'[gap checkpoint] resuming from {checkpoint_path}: '
          f'{len(cache)} previously-computed gap registration(s) loaded, will not be redone.')
    return cache


def _save_checkpoint(checkpoint_path, cache):
    # write-then-replace so a crash mid-write (this whole feature exists
    # because native crashes are abrupt) can't leave a truncated/corrupt
    # checkpoint file that then fails to load on the next attempt.
    tmp_base = checkpoint_path[:-4] if checkpoint_path.endswith('.npy') else checkpoint_path
    np.save(tmp_base + '.tmp', cache, allow_pickle=True)  # np.save appends '.npy' itself
    os.replace(tmp_base + '.tmp.npy', checkpoint_path)


def _timing_checkpoint_path(track_ops):
    """Where cumulative precompute/chain COMPUTE time gets persisted across
    crash+resume attempts, alongside gap_cache_checkpoint.npy. Exists because
    time.monotonic() resets to 0 on every new process launch: without this, a
    crash partway through (native heap corruption -- see module docstring)
    followed by rerunning the same command would have the final [timing]
    summary report only the RESUMED portion's wall time, silently
    understating true total compute time by however long the pre-crash
    attempt ran -- exactly what happened comparing an N_WORKERS=1 run that
    crashed and resumed against a clean single-shot N_WORKERS=10 run. This
    tracks accumulated ACTIVE compute time only (not wall time since the
    very first launch), so idle time between the crash and rerunning it
    doesn't get counted either."""
    return os.path.join(track_ops.save_path, 'gap_timing_checkpoint.npy')


def _load_timing_checkpoint(path):
    if not os.path.exists(path):
        return {'precompute_elapsed': 0.0, 'chain_elapsed': 0.0}
    d = np.load(path, allow_pickle=True).item()
    if d.get('precompute_elapsed', 0.0) > 0 or d.get('chain_elapsed', 0.0) > 0:
        print(f'[timing checkpoint] {path} shows {d.get("precompute_elapsed", 0.0):.1f}s precompute + '
              f'{d.get("chain_elapsed", 0.0):.1f}s chaining already accumulated on this save_path '
              f'(prior crashed/interrupted attempt) -- will add to this attempt\'s time below.')
    return d


def _save_timing_checkpoint(path, d):
    tmp_base = path[:-4] if path.endswith('.npy') else path
    np.save(tmp_base + '.tmp', d, allow_pickle=True)
    os.replace(tmp_base + '.tmp.npy', path)


def _gap_pair_worker(task):
    """Module-level (picklable) unit of work for precompute_gap_pairs_parallel()
    -- must stay top-level, not nested, so worker processes can import and
    locate it by reference under macOS's default 'spawn' start method."""
    i, k, plane_j, roi_ref_raw, roi_mov_raw, ref_img, mov_img, track_ops = task
    ref_ind, reg_ind = _assign_pair(roi_ref_raw, roi_mov_raw, ref_img, mov_img, track_ops)
    return i, k, plane_j, ref_ind, reg_ind


def precompute_gap_pairs_parallel(all_ds_all_roi_ref, all_ds_all_roi_mov, track_ops,
                                   max_gap=3, checkpoint_path=None, n_workers=4, verbose=True):
    """Eagerly registers every possible (i, i+gap) pair for gap in 2..max_gap,
    across all planes, in parallel -- Python's closest equivalent to
    MATLAB's parfor is concurrent.futures.ProcessPoolExecutor, used here.

    Call this BEFORE get_all_pl_match_mat_gap() (run_t2p_gap_tolerant() does
    this automatically when n_workers > 1). Once this has run, every gap
    pair get_all_pl_match_mat_gap() might need is already sitting in the
    checkpoint file, so its own sequential lazy loop just hits cache instead
    of computing anything -- no changes needed to that function at all.

    TRADE-OFF vs. the default lazy/sequential approach: this computes the
    full BOUNDED universe of gap pairs up front (bounded because max_gap is
    small and finite -- roughly max_gap * n_sessions * nplanes total, the
    same "up to N possible" count already printed at the end of a normal
    run), not just the ones any chain actually ends up needing. Some of that
    work may go unused. Whether this is a net win depends on how much
    dropout you actually have and how many workers you can run: on real
    data with substantial dropout (the case this whole fix exists for) it's
    usually a clear win on multi-core hardware -- e.g. registering 40
    possible pairs across 10 workers (4 rounds) beats registering the 10
    pairs actually needed one at a time (10 rounds). With very little
    dropout, eager-parallel can end up doing more *wall-clock* rounds than
    lazy-sequential would have needed -- profile on a small session subset
    first if you're not sure.

    TWO GOTCHAS, both already handled here, but understand them before
    tuning n_workers up:

    1. elastix's compiled registration code very likely does its own
       internal multithreading per call. N worker PROCESSES each also using
       M internal THREADS can oversubscribe your cores and end up SLOWER
       than sequential. This function sets ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS
       and OMP_NUM_THREADS to 1 in THIS (parent) process's environment
       before spawning workers -- spawned children inherit that environment
       at spawn time, before they do their own fresh imports, so ITK never
       sees a chance to grab more than 1 thread per worker. (Setting this
       via a ProcessPoolExecutor `initializer=` callback would be too late
       -- resolving the initializer reference in the child already requires
       importing this module, which imports track2p's elastix bindings as a
       side effect, before the initializer body ever runs.) If you already
       have these env vars set to something else intentionally, this won't
       override them (os.environ.setdefault).

    2. macOS's default 'spawn' start method means each worker process
       re-imports whatever script called this function to locate the work
       function. If THAT SCRIPT (e.g. run_gap_tolerant.py) has top-level
       code that isn't guarded behind `if __name__ == '__main__':`, every
       worker will re-run the entire script from scratch -- including
       spawning its own pool of workers, recursively. run_gap_tolerant.py
       already wraps its logic in main() + the standard guard for exactly
       this reason; if you're calling this from your own script, do the
       same.

    Untested against real elastix/itk in the environment this was written
    in -- validate on a small session subset (and try n_workers=1 vs. a
    few) before trusting it on a big run.
    """
    n_sessions = len(track_ops.all_ds_path)
    n_pairs = n_sessions - 1

    if checkpoint_path is None:
        checkpoint_path = _checkpoint_path(track_ops)
    gap_assign_cache = _load_checkpoint(checkpoint_path)

    # full bounded universe: gap=1 is always the free fast path (already
    # computed by the normal consecutive pass), never needs this.
    todo = []
    for i in range(n_sessions - 1):
        for gap in range(2, max_gap + 1):
            k = i + gap
            if k > n_sessions - 1:
                break
            for plane_j in range(track_ops.nplanes):
                key = (i, k, plane_j)
                if key not in gap_assign_cache:
                    todo.append(key)

    if not todo:
        print('[gap precompute] nothing to do -- checkpoint already covers every possible gap pair.')
        return gap_assign_cache

    print(f'[gap precompute] {len(todo)} gap pair(s) to register in parallel across '
          f'{n_workers} worker(s) (worst case for max_gap={max_gap} -- some may end up unused '
          f'by the actual chains).')

    # gotcha #1 -- see docstring above. Must happen before ProcessPoolExecutor spawns workers.
    os.environ.setdefault('ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS', '1')
    os.environ.setdefault('OMP_NUM_THREADS', '1')

    tasks = []
    for (i, k, plane_j) in todo:
        roi_ref_raw = _raw_roi_array(all_ds_all_roi_ref, all_ds_all_roi_mov, i, plane_j, n_pairs)
        roi_mov_raw = _raw_roi_array(all_ds_all_roi_ref, all_ds_all_roi_mov, k, plane_j, n_pairs)
        ref_img = track_ops.all_ds_avg_ch1[i][plane_j]
        mov_img = track_ops.all_ds_avg_ch1[k][plane_j]
        tasks.append((i, k, plane_j, roi_ref_raw, roi_mov_raw, ref_img, mov_img, track_ops))

    completed = 0
    loop_start = time.monotonic()
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_gap_pair_worker, t): t[:3] for t in tasks}
        for fut in as_completed(futures):
            i, k, plane_j = futures[fut]
            try:
                _, _, _, ref_ind, reg_ind = fut.result()
            except Exception as e:
                print(f'  [gap precompute] WARNING: session {i} -> {k} (plane {plane_j}) '
                      f'failed: {e!r} -- treating as no match (chain will fall back to a '
                      f'longer gap, or truncate there).')
                ref_ind, reg_ind = np.array([], dtype=int), np.array([], dtype=int)
            gap_assign_cache[(i, k, plane_j)] = [ref_ind, reg_ind]
            completed += 1
            if verbose:
                # ETA from the cumulative average rate since this loop started -- the simplest
                # estimator available here, since work is only ever submitted, never re-planned,
                # so there's no per-task cost model to do better with. Held back until at least
                # one full round across all workers has landed (completed >= n_workers), since
                # the very first result or two can finish unusually fast/slow relative to the
                # steady-state rate and produce a wildly wrong early estimate.
                eta_str = ''
                if completed >= max(1, n_workers) and completed < len(todo):
                    elapsed = time.monotonic() - loop_start
                    rate = completed / elapsed if elapsed > 0 else 0
                    if rate > 0:
                        eta_s = (len(todo) - completed) / rate
                        eta_str = f', ETA {eta_s / 60:.1f} min' if eta_s >= 60 else f', ETA {eta_s:.0f}s'
                print(f'  [gap precompute {completed}/{len(todo)}] session {i} -> {k} '
                      f'(plane {plane_j}): {len(ref_ind)} matches above threshold{eta_str}')
            if completed % max(1, n_workers) == 0 or completed == len(todo):
                _save_checkpoint(checkpoint_path, gap_assign_cache)  # periodic flush, not every result

    _save_checkpoint(checkpoint_path, gap_assign_cache)  # final flush, belt-and-suspenders
    return gap_assign_cache


def get_all_pl_match_mat_gap(all_ds_all_roi_ref, all_ds_all_roi_mov, all_ds_assign_thr,
                              track_ops, max_gap=3, verbose=True, checkpoint_path=None):
    """Gap-tolerant replacement for track2p.match.loop.get_all_pl_match_mat.

    Still anchored at session 0 (that's fix #2, a separate change) -- this
    only changes what happens once a chain hits a failed transition: instead
    of stopping, it tries session i -> i+2, i -> i+3, ... i -> i+max_gap
    before truncating for real. A skipped intermediate session is left as
    None in the match matrix (it's genuinely missing for that cell); the
    chain continues from wherever it was re-found.

    Every newly-computed gap registration is persisted to checkpoint_path
    immediately, not just cached in memory -- gap registrations are the
    expensive, crash-prone part of this whole approach (real elastix calls,
    all in one process, potentially hundreds of them on a large/noisy
    session set), so losing that work to any mid-run crash is exactly what
    this guards against. Just rerun the same launcher script pointed at the
    same save_path afterward -- already-cached pairs are skipped
    automatically and it picks up where it left off. If
    precompute_gap_pairs_parallel() already populated the checkpoint (see
    run_t2p_gap_tolerant's n_workers argument), this loop just hits cache
    for every gap pair and runs essentially instantly.
    """
    n_sessions = len(track_ops.all_ds_path)
    n_pairs = n_sessions - 1
    all_pl_match_mat = init_all_pl_match_mat(all_ds_all_roi_ref, all_ds_assign_thr, track_ops)

    if checkpoint_path is None:
        checkpoint_path = _checkpoint_path(track_ops)
    gap_assign_cache = _load_checkpoint(checkpoint_path)  # (i, k, plane_j) -> [ref_ind_thr, reg_ind_thr]

    def get_assign(i, k, plane_j):
        if k == i + 1:
            return all_ds_assign_thr[i][plane_j]  # already computed by the normal consecutive pass
        key = (i, k, plane_j)
        if key not in gap_assign_cache:
            roi_ref_raw = _raw_roi_array(all_ds_all_roi_ref, all_ds_all_roi_mov, i, plane_j, n_pairs)
            roi_mov_raw = _raw_roi_array(all_ds_all_roi_ref, all_ds_all_roi_mov, k, plane_j, n_pairs)
            ref_img = track_ops.all_ds_avg_ch1[i][plane_j]
            mov_img = track_ops.all_ds_avg_ch1[k][plane_j]
            ref_ind, reg_ind = _assign_pair(roi_ref_raw, roi_mov_raw, ref_img, mov_img, track_ops)
            gap_assign_cache[key] = [ref_ind, reg_ind]
            _save_checkpoint(checkpoint_path, gap_assign_cache)  # persist before moving on
            if verbose:
                print(f'  [gap] registered session {i} -> session {k} directly (plane {plane_j}): '
                      f'{len(ref_ind)} matches above threshold')
        return gap_assign_cache[key]

    for plane_j in range(track_ops.nplanes):
        pl_match_mat = all_pl_match_mat[plane_j]

        for roi_idx in range(pl_match_mat.shape[0]):
            if pl_match_mat[roi_idx, 0] is None:
                continue

            track_roi = np.array(pl_match_mat[roi_idx, 0])
            ds_ind = 0
            while ds_ind < n_sessions - 1:
                found = False
                for gap in range(1, max_gap + 1):
                    target = ds_ind + gap
                    if target > n_sessions - 1:
                        break
                    ref_ind, reg_ind = get_assign(ds_ind, target, plane_j)
                    if ref_ind.size == 0:
                        continue
                    reg_ind_ind = np.where(ref_ind == track_roi.item())[0]
                    if reg_ind_ind.size > 0:
                        track_roi = reg_ind[reg_ind_ind]
                        pl_match_mat[roi_idx, target] = track_roi.item()
                        ds_ind = target
                        found = True
                        break
                if not found:
                    break  # exhausted max_gap lookahead from ds_ind -- genuinely stop this track

        n_tracked_all = int(np.sum(np.all(pl_match_mat != None, axis=1)))  # noqa: E711
        n_present = (pl_match_mat != None).sum(axis=1)  # noqa: E711
        n_tracked_1miss = int(np.sum(n_present >= n_sessions - 1))
        print(f'[gap-tolerant, max_gap={max_gap}] plane{plane_j}: '
              f'{n_tracked_all} tracked across all {n_sessions} sessions '
              f'({n_tracked_1miss} with <=1 session missing)')

        track_ops.all_pl_match_mat = all_pl_match_mat
        track_ops.n_tracked = n_tracked_all

    return all_pl_match_mat, gap_assign_cache


def run_t2p_gap_tolerant(track_ops, max_gap=3, n_workers=1):
    """Drop-in alternative to track2p.t2p.run_t2p using gap-tolerant chaining.

    Steps 1-6 (load data, register consecutive pairs, plots) are the
    unmodified track2p pipeline. Only the final assignment/chaining step is
    replaced. Output files (plane{j}_match_mat.npy, track_ops.npy, suite2p
    format export, plots) are written to the same places in the same
    formats as a normal run_t2p call.

    n_workers: 1 (default) keeps the original behavior -- gap registrations
    computed lazily, one at a time, only as chains actually need them.
    Set > 1 to instead precompute the full bounded universe of possible gap
    pairs in parallel first (see precompute_gap_pairs_parallel()'s
    docstring for the trade-off and two gotchas -- in particular, the
    SCRIPT calling this must guard its own top-level code behind
    `if __name__ == '__main__':` on macOS, or every worker process will
    re-run that entire script from scratch).

    Prints wall-clock timing for the run as a whole plus a breakdown of the
    two phases that matter for judging N_WORKERS: the parallel precompute
    step (the only phase N_WORKERS actually affects) and the chaining step
    (fast once the gap cache is warm, regardless of N_WORKERS, since it's
    then just cache lookups + assignment logic, no elastix calls) -- so a
    N_WORKERS=1 vs. N_WORKERS>1 run on the same session list gives you a
    real speedup number instead of having to watch the clock yourself.
    """
    from track2p.t2p import generate_suite2p_indices, save_in_s2p_format

    run_start = time.monotonic()

    track_ops.init_save_paths()
    check_nplanes(track_ops)

    timing_ckpt_path = _timing_checkpoint_path(track_ops)
    timing_ckpt = _load_timing_checkpoint(timing_ckpt_path)
    prior_precompute_elapsed = timing_ckpt.get('precompute_elapsed', 0.0)
    prior_chain_elapsed = timing_ckpt.get('chain_elapsed', 0.0)
    resumed_from_prior_attempt = prior_precompute_elapsed > 0 or prior_chain_elapsed > 0

    if track_ops.input_format == 'npy':
        print('Converting npy data to track2p-compatible format...')
        npy_to_s2p(track_ops)

    all_ds_avg_ch1, all_ds_avg_ch2 = load_all_imgs(track_ops)

    plot_all_planes(all_ds_avg_ch1, track_ops)
    if track_ops.nchannels == 2:
        plot_all_planes(all_ds_avg_ch2, track_ops, ch='anatomical')

    all_ds_ref_img, all_ds_mov_img = get_all_ds_img_for_reg(all_ds_avg_ch1, all_ds_avg_ch2, track_ops)
    all_ds_mov_img_reg, all_ds_reg_params = run_reg_loop(all_ds_ref_img, all_ds_mov_img, track_ops)
    plot_reg_img_output(track_ops)

    all_ds_all_roi_ref, all_ds_all_roi_mov, all_ds_all_roi_reg, all_ds_roi_counter = \
        reg_all_ds_all_roi(all_ds_reg_params, track_ops)

    all_ds_ref_reg_inters = get_all_ref_nonref_inters(all_ds_all_roi_ref, all_ds_all_roi_reg, track_ops)
    all_ds_ref_mov_inters = get_all_ref_nonref_inters(all_ds_all_roi_ref, all_ds_all_roi_mov, track_ops)
    track_ops.all_ds_ref_mov_inters = all_ds_ref_mov_inters
    track_ops.all_ds_ref_reg_inters = all_ds_ref_reg_inters
    if track_ops.show_roi_reg_output:
        plot_roi_reg_output(track_ops)

    # consecutive-pair (gap=1) assignment -- unmodified track2p logic, this seeds
    # both the session-0 anchor and the fast path inside get_assign() above
    all_ds_assign, all_ds_assign_thr, all_ds_thr_met, all_ds_thr = \
        get_all_ds_assign(track_ops, all_ds_all_roi_ref, all_ds_all_roi_reg)
    plot_thr_met_hist(all_ds_thr_met, all_ds_thr, track_ops)
    plot_n_matched_roi(all_ds_thr_met, all_ds_thr, track_ops)

    precompute_elapsed = None
    if n_workers > 1:
        print(f'\n[gap] precomputing the full bounded gap-pair universe in parallel '
              f'({n_workers} workers) before chaining...')
        precompute_start = time.monotonic()
        precompute_gap_pairs_parallel(all_ds_all_roi_ref, all_ds_all_roi_mov, track_ops,
                                       max_gap=max_gap, n_workers=n_workers)
        precompute_elapsed = time.monotonic() - precompute_start
        timing_ckpt['precompute_elapsed'] = prior_precompute_elapsed + precompute_elapsed
        _save_timing_checkpoint(timing_ckpt_path, timing_ckpt)
        print(f'[gap] parallel precompute finished in {precompute_elapsed:.1f}s this attempt '
              f'({n_workers} workers, max_gap={max_gap})'
              + (f' -- {timing_ckpt["precompute_elapsed"]:.1f}s cumulative on this save_path'
                 if resumed_from_prior_attempt else ''))

    # *** fix #1: gap-tolerant chaining in place of get_all_pl_match_mat ***
    chain_start = time.monotonic()
    all_pl_match_mat, gap_assign_cache = get_all_pl_match_mat_gap(
        all_ds_all_roi_ref, all_ds_all_roi_mov, all_ds_assign_thr, track_ops, max_gap=max_gap)
    chain_elapsed = time.monotonic() - chain_start
    timing_ckpt['chain_elapsed'] = prior_chain_elapsed + chain_elapsed
    _save_timing_checkpoint(timing_ckpt_path, timing_ckpt)
    print(f'[gap] chaining step finished in {chain_elapsed:.1f}s this attempt '
          f'({"warm cache from precompute above" if precompute_elapsed is not None else "lazy, sequential gap registration as needed"})'
          + (f' -- {timing_ckpt["chain_elapsed"]:.1f}s cumulative on this save_path'
             if resumed_from_prior_attempt else ''))

    save_track_ops(track_ops)
    save_match_diagnostics(all_ds_thr_met, all_ds_thr, track_ops)
    save_all_pl_match_mat(all_pl_match_mat, track_ops)

    print('Generating suite2p indices')
    generate_suite2p_indices(track_ops)

    if track_ops.save_in_s2p_format:
        print('Saving in suite2p format...')
        save_in_s2p_format(track_ops)

    print('Finished with algorithm!\n\nGenerating plots (this can take some time)...\n\n')
    all_ds_stat_iscell = load_all_ds_stat_iscell(track_ops)
    all_ds_centroids = load_all_ds_centroids(all_ds_stat_iscell, track_ops)
    all_ds_mean_img = load_all_ds_mean_img(track_ops)
    if track_ops.nchannels == 2:
        all_ds_mean_img_ch2 = load_all_ds_mean_img(track_ops, ch=2)

    plot_roi_match_multiplane(all_ds_mean_img, all_ds_centroids, all_pl_match_mat, track_ops,
                               win_size=track_ops.win_size)
    plot_allroi_match_multiplane(all_ds_mean_img, all_pl_match_mat, track_ops)
    if track_ops.nchannels == 2:
        plot_roi_match_multiplane(all_ds_mean_img_ch2, all_ds_centroids, all_pl_match_mat, track_ops,
                                   win_size=track_ops.win_size, ch=2)
        plot_allroi_match_multiplane(all_ds_mean_img_ch2, all_pl_match_mat, track_ops, ch=2)

    print(f'\nGap-tolerant run complete (max_gap={max_gap}): '
          f'{len(gap_assign_cache)} extra non-adjacent session pairs registered on demand '
          f'(out of up to {sum(min(max_gap, len(track_ops.all_ds_path) - 1 - i) - 1 for i in range(len(track_ops.all_ds_path) - 1))} possible).')

    total_elapsed = time.monotonic() - run_start
    print(f'\n[timing] total run_t2p_gap_tolerant wall time (this process): {total_elapsed / 60:.1f} min '
          f'({total_elapsed:.0f}s), n_workers={n_workers}, max_gap={max_gap}')
    if precompute_elapsed is not None:
        print(f'[timing]   precompute (this process): {precompute_elapsed / 60:.1f} min '
              f'({precompute_elapsed:.0f}s) -- this is the phase N_WORKERS actually speeds up')
    print(f'[timing]   chaining (this process): {chain_elapsed / 60:.1f} min ({chain_elapsed:.0f}s)')
    if resumed_from_prior_attempt:
        cumulative_precompute = timing_ckpt.get('precompute_elapsed', 0.0)
        cumulative_chain = timing_ckpt.get('chain_elapsed', 0.0)
        print(f'\n[timing] NOTE: this save_path had a prior crashed/interrupted attempt -- "this process" '
              f'above only covers work done SINCE the resume, so it understates true total compute time. '
              f'Cumulative gap-registration compute time across ALL attempts on this save_path (idle time '
              f'between attempts excluded, and NOT including data-loading/consecutive-pair-registration time '
              f'from the crashed attempt, which redoes on resume and isn\'t itself checkpointed):')
        print(f'[timing]   precompute cumulative: {cumulative_precompute / 60:.1f} min ({cumulative_precompute:.0f}s)')
        print(f'[timing]   chaining cumulative:   {cumulative_chain / 60:.1f} min ({cumulative_chain:.0f}s)')
        print(f'[timing]   Use these cumulative numbers, not "this process" above, when comparing against an '
              f'N_WORKERS run that completed cleanly in one shot.')

    print('Done!\n')

    return all_pl_match_mat
