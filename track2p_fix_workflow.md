# track2p tracking-failure workflow

Reusable procedure for diagnosing and recovering yield on a track2p run, built from the wehr5336 1-9 session investigation and confirmed to generalize on a second mouse (wehr5917). Re-run this same sequence for new session-count checkpoints, and for any other mouse.

All scripts are session-count-agnostic; just point them at new `save_path` directories.

## Setup (once per rig/protocol)

Generate a central settings file so you never have to hand-type track2p settings or borrow a whole `track_ops.npy` again:

```
python track_ops_config.py --export "/path/to/some/existing/track2p/track_ops.npy" track2p_settings.cfg
```

Edit `track2p_settings.cfg` by hand if a setting ever needs to change. Every launcher script below prefers `TRACK_OPS_CFG` pointed at this file; `SETTINGS_SOURCE_PATH` (borrowing straight from an existing `track_ops.npy`) still works as a legacy fallback.

## Pre-acquisition check (optional, prevents the problem instead of cleaning up after it)

`preflight_registration_check.m` (MATLAB) checks FOV alignment against a reference session BEFORE committing to a full longitudinal acquisition + suite2p run. Reads a short (~1000 frame, configurable) raw `.sbx` clip plus the reference session's own raw `.sbx` file directly -- no suite2p processing needed for either one, since the rig computer may not have suite2p output available/reachable at check time, and there's no guarantee a same-day reference session has finished processing yet. Runs entirely in MATLAB, shelling out to a standalone `elastix` CLI install (via a one-time-exported parameter file from `export_elastix_params.py`) rather than bridging to Python at check-time, so the rig needs zero Python.

Setup once (or whenever `track_ops.transform_type` changes), from the track2p conda env:
```
python export_elastix_params.py --cfg track2p_settings.cfg elastix_params.txt
```
Copy `elastix_params.txt` to the rig; point `ELASTIX_PARAMS_FILE` at it. See that script's own docstring for a real caveat: it's SimpleElastix's standard parameter map for your transform type, not a verified line-for-line match to `reg_img_elastix.py`'s actual settings -- diff the two by hand once.

**Known Windows gotcha, confirmed on real rig hardware:** MATLAB's `system()` call can fail to find `elastix` on PATH even when a fresh terminal finds it fine -- MATLAB inherits the environment from when it launched, so a PATH edit made afterward (or in a different scope) silently doesn't apply. Set `ELASTIX_BIN` in the script's config to the FULL PATH to `elastix.exe` (find it via `where elastix` in a working terminal) rather than relying on PATH at all -- the script also detects this specific failure and prints a targeted message if it happens anyway.

Optional `DO_MOTION_CORRECTION` config flag rigid-aligns frames (FFT phase correlation) before averaging each short clip, since raw `.sbx` frames aren't suite2p-registered the way a real `meanImg` would be -- off by default, worth turning on if within-session jitter blur turns out to matter for your rig/data.

## 0. Build your session list and run a cheap vanilla-equivalent pass

Don't type session paths out by hand — use `find_session_dirs()` (from `session_order_utils.py`) to scan your raw data folder(s):

```python
from session_order_utils import find_session_dirs
ALL_DS_PATH = find_session_dirs('/path/to/raw_data')
```

This also handles two easy-to-miss traps automatically:
- **Mapping-day sessions.** By default (`exclude_earliest_date=True`) it drops every session on the chronologically earliest date found, since your recording convention is that day 1 is mapping-only regardless of suffix. Pass `name_pattern=`/`exclude_pattern=` too if a subject has a second FOV series.
- **Bad-directory sanity checks.** It warns if a path runs through a `matched_suite2p` folder (track2p's own prior output, not raw data) or if any session's ROI count looks suspicious relative to the group — check before spending registration compute.

**`find_session_dirs()` vs. `load_all_ds_path()` — don't mix these up on a brand-new mouse.** They look interchangeable (both return an `ALL_DS_PATH` list) but read from opposite kinds of directory: `find_session_dirs(parent_dir)` scans a folder of **raw** per-session suite2p subfolders — use this here, at step 0, for a subject with no prior track2p run. `load_all_ds_path(save_path)` instead reads the session list back out of an **existing track2p output**'s `track_ops.npy` — only valid once step 0 has actually produced one (used later, e.g. chaining exclusion rounds in step 3). Pointing `load_all_ds_path()` at a raw data folder fails immediately with `FileNotFoundError: No track_ops.npy at ...` since there's no track2p run there yet.

Run `run_gap_tolerant.py` with **`MAX_GAP = 1`** rather than the GUI. This is not an approximation of vanilla — it *is* vanilla: with `max_gap=1`, `get_all_pl_match_mat_gap`'s only gap value is 1, so every transition takes the already-computed consecutive-pair fast path and zero extra elastix calls ever fire (confirmed in `fix1_gap_tolerant_chain.py`'s own end-of-run accounting, which reports 0 possible gap pairs at `max_gap=1`). Same registration cost, same output, as plain `track2p.t2p.run_t2p()` — but routed through `find_session_dirs`/`TRACK_OPS_CFG`/`ensure_chronological_order`/checkpointing instead of hand-driving the GUI. No separate GUI run needed anywhere in this workflow anymore; screening, exclusion (step 3, via `run_exclude_session.py`), and the vanilla baseline used for comparison in step 6 can all just be `MAX_GAP=1` runs of `run_gap_tolerant.py`.

Screen and exclude on cheap `MAX_GAP=1` runs first, then pay for real gap-tolerant chaining (`MAX_GAP` > 1) once, at the end, on the cleaned list — running the expensive version first, on a session list that hasn't been screened yet, means paying for extra registrations on exactly the sessions most likely to be bad, which is the worst case for triggering the heap-corruption crash noted in step 5 below.

This produces, in `<save_path>/track2p/`:
- `track_ops.npy`
- `plane{j}_match_mat.npy`
- `match_diagnostics.npy` (from your local patch — per-transition IOU/threshold data)

Note the strict-AND yield it reports. This is your baseline, and it will look bad at higher session counts purely from the p^(N-1) exponent — don't read a low number here as a failure yet.

## 1. Screen for suspect sessions

```
python screen_sessions.py <save_path>
python registration_quality_scan.py <save_path>
```

All signals from `screen_sessions.py` are available immediately after step 0 (cell count, image sharpness, neighbor-transition match rate, dominant-missing-session all read from files already on disk). Look for sessions flagged with more than one criterion, especially `BAD_NEIGHBOR_TRANSITIONS` — that's the more specific fingerprint. A lone `DOMINANT_MISSING_SESSION` flag can just be downstream fallout from a different session breaking the chain (this happened with session 8 before session 7 was excluded).

**Run `registration_quality_scan.py` every time too, not just when something's already flagged.** It measures something `screen_sessions.py`'s neighbor rate structurally cannot: neighbor rate comes from Otsu thresholding applied per-pair, which just finds *a* locally-separable split in that pair's IOU distribution — it has no absolute reference for what a real match looks like, so a uniformly bad registration can still produce a plausible-looking match rate if Otsu finds *some* threshold, even when the "matches" are essentially noise. This is exactly how a genuinely broken transition (near-zero image-level overlap, confirmed visually) slipped past `screen_sessions.py` entirely on a real run, while `registration_quality_scan.py`'s SSIM score caught it. The two tools are checking different things and neither subsumes the other — always run both.

**Any pair `registration_quality_scan.py` flags also gets an automatic phase-correlation follow-up** — a cheap FFT-based check (not sensitive to displacement magnitude the way elastix's gradient-descent optimizer is) for whether the failure is a *capture-range* problem (the true displacement is large but the data actually aligns fine — elastix's optimizer just couldn't find it from its starting point) rather than genuine misalignment. A `CAPTURE-RANGE?` annotation on a flagged grid row means this — worth checking before assuming the session itself is bad. This surfaced for real on wehr5917's session 6->7 pair (elastix SSIM 0.031, phase-corr SSIM 0.591, 64px recovered shift, confirmed visually) — see below for what to do about a confirmed capture-range failure.

## 2. Visually confirm suspects

```
python compare_session_qc.py <save_path>
```
This writes `session_qc_images.png` (mean image per session, 1st-99th percentile contrast) and `session_qc_counts.png` (iscell count bar chart, sessions below 50% of the group median highlighted in red) into `<save_path>/diagnostics/`. Pass `--sessions` to restrict the image panel to specific sessions (0-indexed integers and/or date/substrings, same convention as `inspect_registration_pair.py`) — the count chart always covers every session regardless. Never exclude a session on the numeric flags alone — confirm the mean image actually looks degraded, or the cell count is genuinely low relative to neighbors, not just statistically unusual.

(`export_session_qc.py` + `compare_session_qc.m` still work if you'd rather do this step in MATLAB — `compare_session_qc.py` reads the same suite2p output directly instead of round-tripping through a `.mat` file, but the two checks are the same.)

**If `registration_quality_scan.py` flagged a pair, also run `inspect_registration_pair.py` on it before deciding anything.** `export_session_qc.py` only shows each session's own raw mean image side by side, which cannot reveal a registration/alignment problem — a session can look completely normal in isolation (fine cell count, sharp image) while genuinely failing to register against its neighbor. This has gone both directions in practice: it's caught a session that looked fine in isolation but had a real alignment failure, and it's the only thing that can confirm (or rule out) a flag from `registration_quality_scan.py`, whose absolute SSIM values aren't yet well-calibrated enough to trust without a visual check.

**If the flag looks like a capture-range failure (`CAPTURE-RANGE?` in the grid, or a notably higher phase-corr SSIM than elastix's), confirm with `debug_large_displacement.py --ref <i> --mov <k>`** — same idea as `inspect_registration_pair.py` but runs elastix and phase correlation side by side, with an overlay panel for each, so you can see directly whether the phase-corr alignment looks genuinely good (yellow/white) rather than trusting the SSIM numbers alone.

**A confirmed capture-range failure doesn't have to mean excluding the session.** Since the underlying data aligns fine — elastix's optimizer just couldn't find the shift — `apply_shift_correction.py` pre-corrects the affected session's suite2p output (its `meanImg` and ROI pixel coordinates) by the phase-correlation-recovered shift *before* track2p ever registers it, so track2p's own elastix call only has to handle the small residual instead of the full displacement:
```
python apply_shift_correction.py /path/to/mov_session_dir --ref /path/to/ref_session_dir --plane 0 \
    --out /path/to/mov_session_dir_shift_corrected
```
It computes the shift itself (same phase correlation as above) and refuses to write output if the correction doesn't show a real improvement (`--min-ssim-gain`, default 0.1 — `--force` to override once you've confirmed visually despite a marginal number). Point `ALL_DS_PATH` at the corrected folder in place of the original for your next run. Worth doing the arithmetic before deciding whether it's worth the extra step: back-solve how many strict-AND cells the fix would plausibly recover (see wehr5917's 6->7 case in `SESSION_LOG.md` for the method) — sometimes it's a large gain, sometimes it genuinely doesn't matter (e.g. a last-session failure that gap-tolerant chaining couldn't have used anyway — see the structural facts below).

Only ONE session in a pair should ever need correcting, not both — pick whichever one is more convenient (e.g. whichever already has suite2p output, or whichever isn't itself also implicated in a DIFFERENT flagged pair). If chaining this with an exclusion round on a DIFFERENT session (via step 3 below), substitute the corrected path by date/substring match, not list index — excluding an earlier session shifts every later session's position.

## 3. Exclude confirmed bad sessions, one at a time

Edit and run `run_exclude_session.py`:
- `TRACK_OPS_CFG` (preferred) or `SETTINGS_SOURCE_PATH` (legacy) → settings source
- `ALL_DS_PATH` → the session list being screened (required if using `TRACK_OPS_CFG`; leave `None` under `SETTINGS_SOURCE_PATH` to just reuse that run's own list)
- `NEW_BASE_PATH` → a **new** directory (never the parent of your settings source — it collides and overwrites)
- `EXCLUDE_MATCH` → date/substring uniquely identifying the session

If excluding more than one session, chain the calls: point the next round's `SETTINGS_SOURCE_PATH` at the previous round's output (`.../track2p`), or, if using `TRACK_OPS_CFG`, set `ALL_DS_PATH = load_all_ds_path('.../prev_round/track2p')` (from `session_order_utils.py`) — **not** `find_session_dirs()`, which scans for raw dated session folders and silently returns an empty list when pointed at a track2p output directory instead (it has no per-session subfolders — just `track_ops.npy`, `match_mat.npy`, plots).

## 4. Re-screen after each exclusion

Go back to step 1 on the new output. Removing one bad session can reveal a second one that was previously masked (this is exactly how 02-03-26 was found, only after 02-24-26 was removed). Loop steps 1-4 until `screen_sessions.py` comes back clean, or any remaining flags are visually confirmed as real biology rather than a data problem.

## 5. Run gap-tolerant chaining on the cleaned session list

Edit and run `run_gap_tolerant.py`:
- `TRACK_OPS_CFG` (preferred) or `SETTINGS_SOURCE_PATH` (legacy) → settings source
- `ALL_DS_PATH` → your final cleaned session list from step 4 (required under `TRACK_OPS_CFG`)
- `NEW_BASE_PATH` → new directory
- `MAX_GAP` → start with 2 or 3

**If it crashes with a native `malloc: Heap corruption detected` / `zsh: abort` (not a Python traceback):** this is memory corruption inside the compiled elastix bindings, triggered by running many registration calls in one long-lived process — more likely the larger/noisier your session list, since gap-tolerant chaining's extra registrations scale with how much dropout there is. It's checkpointed: every gap registration is written to `gap_cache_checkpoint.npy` in the run's save folder as soon as it's computed, so just rerun `run_gap_tolerant.py` unchanged (same `NEW_BASE_PATH`) — it prints `[gap checkpoint] resuming from ...` and skips everything already done rather than starting over. The initial consecutive-pair pass (before the gap phase) isn't checkpointed, but it's cheap (N-1 registrations) so redoing it on resume is fine.

## 6. Confirm it did real work

```
python compare_gap_vs_vanilla.py <cleaned MAX_GAP=1 save_path> <gap-tolerant save_path>
```

Use your last `MAX_GAP=1` run on the cleaned session list (step 4's final output) as the vanilla side — no need to run anything new for this. Expect nonzero "rows with MORE sessions present" and genuine mid-track holes. The strict-AND count will **not** move and the exact-same-cells line will read `True` — that's structurally guaranteed, not a failure (see conversation history for why). Don't re-litigate that each time; just check the mid-track-hole count is nonzero as confirmation the algorithm engaged.

## 7. Get the practical, usable dataset

```
python fix3_partial_tracks.py <gap-tolerant save_path>
```

This is the number that actually matters for downstream use — the K-based recovery curve, plus exported `plane{j}_match_mat_partial_K{K}.npy`/`.csv` files ready to use directly. Pick a K based on how much per-cell missingness your downstream analysis can tolerate.

## 8. (Optional) Gauge whether fix #2 is worth building

```
python estimate_fix2_ceiling.py <gap-tolerant save_path>
```

Read the "proj. survive to end" column relative to how many transitions remain from each anchor session — a high value from a late-anchored session mostly reflects short 2-3 session snippets, not long-range value. Worth tracking this across the 9/13/18-session checkpoints to see whether the case for fix #2 strengthens as session count grows.

## Known structural facts worth remembering mid-analysis

- Vanilla track2p's chaining is permanently-truncating and forward-only: a cell's "sessions present" is always a contiguous run starting at session 0. There is no such thing as a vanilla row with a gap in the middle.
- Gap-tolerant chaining can only skip forward over a bad transition — it can never recover a failure at the very last session in the list, no matter how large `max_gap` is. Consequence, proven not just observed: since vanilla chaining is always a contiguous prefix, being "1 session short" under vanilla can ONLY mean missing the last session — so "0 of N near-miss rows rescued" from `compare_gap_vs_vanilla.py` is mathematically guaranteed whenever any near-miss rows exist, on every dataset, regardless of `max_gap` or any other transition's quality. Confirmed independently on both wehr5336 (`03-10-26`) and wehr5917. Don't chase it as a data-quality signal — `compare_gap_vs_vanilla.py`'s own printed hint accounts for this now.
- A pair can be flagged as misaligned for two different reasons that need different fixes: genuine misalignment (data doesn't actually line up — exclude a session) vs. a capture-range failure (data aligns fine, elastix's gradient-descent optimizer just couldn't find a large-enough shift — `apply_shift_correction.py` can fix this without losing the session). `registration_quality_scan.py`'s automatic phase-correlation follow-up on flagged pairs (see step 1) distinguishes the two.
- Any row that needed even one gap-jump anywhere in its chain has a permanent hole at the skipped session, so it can never count toward strict-AND completion — this is why gap-tolerant chaining's own strict-AND count is always identical to vanilla's, and why fix #3's K<N counts are the real measure of gap-tolerant chaining's benefit.
- The track2p GUI never sorts sessions by date — a list built across more than one GUI session can silently end up chronologically out of order, which corrupts registration since track2p only compares list-adjacent sessions. `find_session_dirs()` + `ensure_chronological_order()` (baked into both launcher scripts) catch and fix this automatically.
- Watch for `ALL_DS_PATH` accidentally pointing at a `matched_suite2p` folder instead of raw data — it mirrors real session folder names exactly, so glob/date matching alone won't catch it. `find_session_dirs()`'s sanity check does.
- Gap-tolerant chaining on a large/noisy session list can crash the whole process with native heap corruption (many elastix calls accumulating in one process) — this is checkpointed and resumable, not a correctness bug; just rerun the same command.
- `run_gap_tolerant.py` with `MAX_GAP=1` is not an approximation of vanilla track2p, it's identical output at identical cost (see step 0) — there's no remaining reason to launch vanilla runs through the GUI at all.
