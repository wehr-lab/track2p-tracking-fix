# track2p tracking-failure workflow

Reusable procedure for diagnosing and recovering yield on a track2p run, built from the wehr5336 1-9 session investigation. Re-run this same sequence for the 13- and 18-session checkpoints, and for any other mouse.

All scripts are session-count-agnostic; just point them at new `save_path` directories.

## Setup (once per rig/protocol)

Generate a central settings file so you never have to hand-type track2p settings or borrow a whole `track_ops.npy` again:

```
python track_ops_config.py --export "/path/to/some/existing/track2p/track_ops.npy" track2p_settings.cfg
```

Edit `track2p_settings.cfg` by hand if a setting ever needs to change. Every launcher script below prefers `TRACK_OPS_CFG` pointed at this file; `SETTINGS_SOURCE_PATH` (borrowing straight from an existing `track_ops.npy`) still works as a legacy fallback.

## 0. Build your session list and run a cheap vanilla-equivalent pass

Don't type session paths out by hand — use `find_session_dirs()` (from `session_order_utils.py`) to scan your raw data folder(s):

```python
from session_order_utils import find_session_dirs
ALL_DS_PATH = find_session_dirs('/path/to/raw_data')
```

This also handles two easy-to-miss traps automatically:
- **Mapping-day sessions.** By default (`exclude_earliest_date=True`) it drops every session on the chronologically earliest date found, since your recording convention is that day 1 is mapping-only regardless of suffix. Pass `name_pattern=`/`exclude_pattern=` too if a subject has a second FOV series.
- **Bad-directory sanity checks.** It warns if a path runs through a `matched_suite2p` folder (track2p's own prior output, not raw data) or if any session's ROI count looks suspicious relative to the group — check before spending registration compute.

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

## 2. Visually confirm suspects

```
python export_session_qc.py <save_path>
```
then open `session_qc.mat` in MATLAB with `compare_session_qc.m`. Never exclude a session on the numeric flags alone — confirm the mean image actually looks degraded, or the cell count is genuinely low relative to neighbors, not just statistically unusual.

**If `registration_quality_scan.py` flagged a pair, also run `inspect_registration_pair.py` on it before deciding anything.** `export_session_qc.py` only shows each session's own raw mean image side by side, which cannot reveal a registration/alignment problem — a session can look completely normal in isolation (fine cell count, sharp image) while genuinely failing to register against its neighbor. This has gone both directions in practice: it's caught a session that looked fine in isolation but had a real alignment failure, and it's the only thing that can confirm (or rule out) a flag from `registration_quality_scan.py`, whose absolute SSIM values aren't yet well-calibrated enough to trust without a visual check.

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
- Gap-tolerant chaining can only skip forward over a bad transition — it can never recover a failure at the very last session in the list, no matter how large `max_gap` is.
- Any row that needed even one gap-jump anywhere in its chain has a permanent hole at the skipped session, so it can never count toward strict-AND completion — this is why gap-tolerant chaining's own strict-AND count is always identical to vanilla's, and why fix #3's K<N counts are the real measure of gap-tolerant chaining's benefit.
- The track2p GUI never sorts sessions by date — a list built across more than one GUI session can silently end up chronologically out of order, which corrupts registration since track2p only compares list-adjacent sessions. `find_session_dirs()` + `ensure_chronological_order()` (baked into both launcher scripts) catch and fix this automatically.
- Watch for `ALL_DS_PATH` accidentally pointing at a `matched_suite2p` folder instead of raw data — it mirrors real session folder names exactly, so glob/date matching alone won't catch it. `find_session_dirs()`'s sanity check does.
- Gap-tolerant chaining on a large/noisy session list can crash the whole process with native heap corruption (many elastix calls accumulating in one process) — this is checkpointed and resumable, not a correctness bug; just rerun the same command.
- `run_gap_tolerant.py` with `MAX_GAP=1` is not an approximation of vanilla track2p, it's identical output at identical cost (see step 0) — there's no remaining reason to launch vanilla runs through the GUI at all.
