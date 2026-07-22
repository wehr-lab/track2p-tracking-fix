# Session log

Running log of work sessions on the track2p tracking-fix project. Newest entries at the top.

---

## 2026-07-21

### Round 3 exclusion (session 4 / 12-09-25) -- real, partial win

- Ran `run_exclude_session.py` (settings already configured from yesterday) -> `track2p_1-18gap3-skip3` (15 sessions), then `run_gap_tolerant.py` (`MAX_GAP=3`, `N_WORKERS=6` -- first real use of the parallel precompute feature) -> `track2p_1-18gap3-skip3_2`.
- Strict-AND went from 0 (16 sessions) to 6 (15 sessions) -- a genuine, concrete win, not just noise. K-based recovery curve also shifted up substantially across the board (K=8: 103 -> 168 cells; K=14 now recommended, 18 cells).
- BUT: yesterday's read of session 5 (`12-16-25`) as pure fallout from session 4 was wrong. After excluding session 4, `12-16-25`'s dominant-missing percentage went UP (59.9% -> 64.5%), not down -- real evidence it has its own independent problem, not inherited from session 4.

### New finding: session 0->1 (`11-13-25` -> `11-18-25`) is genuinely, badly misregistered

- Built `registration_quality_scan.py` (batch SSIM across every consecutive pair, robust z-score flagging) after the user rightly pushed back on "eyeballing red/green vs. one control pair" as too subjective.
- First version (whole-image SSIM) flagged pair 0->1 as the worst in the entire list, with a wildly negative z-score and no corroboration anywhere else (never flagged by `screen_sessions.py`, normal cell counts/sharpness/neighbor-rate every run). Assumed this was a metric artifact (SSIM dominated by independent background noise in sparse two-photon images) and shipped a masked-SSIM fix (`registration_qc_utils.py`, restricts scoring to the ref image's brightest 20% of pixels) to address it.
- Masking did NOT fix it -- score got worse (0.161 -> 0.041), and the rest of the list still didn't show a clean separation. My synthetic validation test was too clean (toy images, not representative of real 2p mean image statistics) to have actually confirmed the fix would generalize.
- Asked the user to check the actual overlay from `inspect_registration_pair.py --ref 0 --mov 1` as ground truth, independent of the metric. **Confirmed visually: "all red and green, almost no overlap."** The flag was real. I was wrong to be skeptical of it just because it lacked corroboration from `screen_sessions.py`.
- Why `screen_sessions.py` missed this: its neighbor-rate signal comes from Otsu thresholding applied per-pair, adaptively -- it just finds *a* locally-separable split in whatever IOU distribution it's given, with no absolute reference for what a real match looks like. A uniformly bad registration can still produce a plausible-looking match rate (session 0/1 both read totally normal, ~48-56%) if Otsu finds *some* threshold, even when the underlying "matches" are essentially noise.
- **Why this matters more than session 4:** session 0 is the anchor for literally every tracked row in the whole pipeline (everything starts there by construction). If this transition is fundamentally broken, it's plausibly a significant piece of why yield has looked bad from the very first 9-session vanilla baseline (6 cells), before any of this troubleshooting started -- not just "one more bad session," but a compromised foundation the entire chain sits on.
- `track2p_fix_workflow.md` updated: `registration_quality_scan.py` promoted to a standard step-1 screening tool (not just reactive), and step 2 now explicitly calls out that `export_session_qc.py` cannot reveal alignment problems -- `inspect_registration_pair.py` is required to confirm or rule out anything `registration_quality_scan.py` flags.

### New tooling: full-list screening grid, Python-only session QC, timing/ETA

- `registration_quality_scan.py` now also writes a grid PNG (`diagnostics/registration_quality_grid.png`) -- one row per consecutive pair (ref / mov-before-reg / overlay), flagged rows highlighted in red -- so the whole session list can be screened visually in one image instead of opening N-1 separate `inspect_registration_pair.py` outputs. `--middle-panel mov_reg` swaps the middle column; `--no-grid` skips it.
- Fixed a real bug found while building the above: `registration_quality_scan.py` never called `ensure_chronological_order()` on `all_ds_path` (unlike the launcher scripts), so a misordered `track_ops.npy` could silently produce grid/table rows out of date order. Now sorts via `load_all_ds_path` + `ensure_chronological_order`, same guard as everywhere else.
- New `compare_session_qc.py`: pure-Python replacement for the `export_session_qc.py` -> `.mat` -> `compare_session_qc.m` round trip. Reads suite2p output directly, writes `session_qc_images.png` + `session_qc_counts.png`, supports index/date-substring `--sessions` like `inspect_registration_pair.py`. `export_session_qc.py`/`compare_session_qc.m` left in place as an explicitly-labeled legacy MATLAB-side alternative, not deleted. `track2p_fix_workflow.md` step 2 and `screen_sessions.py`'s triage messages now point at the new tool.
- `run_t2p_gap_tolerant()` now prints wall-clock timing (total + precompute vs. chaining breakdown) so an `N_WORKERS` comparison doesn't require watching the clock. Found and fixed a real gap in this the same day: a native heap-corruption crash + checkpoint-resume (see below) was resetting the timer, silently under-reporting true compute time. Fixed by persisting cumulative precompute/chain time across resumes in `gap_timing_checkpoint.npy` (same atomic-write pattern as `gap_cache_checkpoint.npy`) -- verified with an isolated crash+resume simulation, not just a clean run.
- Added ETA to the `[gap precompute X/Y]` parallel-precompute progress line (cumulative-average-rate estimator, held back until one full round across all workers has landed). The other progress lines mentioned (`Transforming ROIs...`, `Finding matches in ref-reg pair...`) come from the `track2p` library itself (`/Users/wehr/git/track2p`), not this repo -- out of reach from here.
- Fixed a stale `strict-AND(9)` label in `compare_gap_vs_vanilla.py` -- leftover literal from the original 9-session dev/test scaffolding, printed regardless of actual session count. Now uses the real `n_sessions`.
- `N_WORKERS` parallel precompute **now validated against real elastix** -- user ran `N_WORKERS=6` successfully. (Removes the "never validated" caveat from 2026-07-20's parked list.)

### Round 4 exclusion (session 0 / `11-13-25`) -- confirmed real, not a mapping-day artifact

- Excluded `11-13-25` -> `track2p_1-18gap3-skip4` (14 sessions), then gap-tolerant chaining -> `..._skip4_2`. Strict-AND: 6 -> 9. K=13 (1 session allowed missing) recovers 28 cells.
- User confirmed `11-13-25` was a genuine longitudinal recording session, not a residual mapping day -- rules out the mapping-day-slipped-past-filtering hypothesis. The misregistration is a real, unexplained acquisition/registration problem, not a data-hygiene artifact.
- `missing_session_histogram.py` on the 14-session list flagged TWO dominant sessions: `12-16-25` (74.2%) and `03-10-26` (77.5%, the last session chronologically). Flagged the hypothesis that a late-list session's high "missing" % can be a structural artifact of forward-only permanent-truncation chaining (every upstream break also removes it) rather than its own independent problem.
- Re-ran `registration_quality_scan.py` on the 14-session list: **both** of `12-16-25`'s neighbor transitions flagged (2->3 SSIM=0.150 z=-2.4, 3->4 SSIM=0.114 z=-2.6) -- the tool's own strongest-evidence signature (a session showing up on both sides). `12-23-25` only flagged on the side shared with `12-16-25` (its other side, 4->5, was unremarkable) -- collateral, not independently bad. `03-10-26`'s transition (12->13, z=0.3) was clean -- confirmed the structural-artifact hypothesis, not a real problem.
- Addressed a "is this a goose chase" concern (4/18 sessions excluded by this point): the z-scores outside the two flagged pairs ran a tight -1.2 to 2.1 with nothing else borderline -- the signature of a small number of genuinely bad sessions in an otherwise consistent list, not a continuous degradation gradient that would never converge. Proposed and used a concrete stopping rule (exclude, re-screen once, stop if nothing crosses threshold or nothing visually catastrophic).

### Round 5 exclusion (`12-16-25`) -- final cleaned list for this checkpoint

- Excluded `12-16-25` -> `track2p_1-18gap3-skip5` (13 sessions), gap-tolerant chaining -> `..._skip5_2`.
- `compare_gap_vs_vanilla.py`: strict-AND 14 both ways (same exact cells, as always structurally guaranteed). 342/1078 candidate ROIs (~32%) gained sessions under gap-tolerant -- a solid partial-track win. 0 of 9 near-miss rows (12/13 present under vanilla) got fully rescued.
- Checked directly: all 9 near-miss rows are missing exactly `03-10-26` -- confirms this is the documented structural limit (gap-tolerant chaining can never rescue a failure at the very last session in the list, no `max_gap` can fix that) and not a new problem. Closed, not worth further chasing.
- **Total exclusions this checkpoint: 5 of 18 original sessions** (`02-03-26`, `02-24-26`, `12-09-25`, `11-13-25`, `12-16-25`), leaving 13 clean sessions. Every flag raised by `screen_sessions.py`/`registration_quality_scan.py` across all 5 rounds has now been either confirmed-and-excluded or confirmed-as-not-a-real-problem (collateral, or structural/positional) -- none left unexplained.
- `fix3_partial_tracks.py` on `skip5_2` (final result for this checkpoint): strict-AND **14** (up from the original 9-session baseline's 6 -- and on 13 sessions instead of 9, which counts for more given the p^(N-1) decay). K-based recovery: K=12 (1 session allowed missing) -> 45 cells, K=11 -> 102, K=10 -> 158, K=9 -> 206, K=8 -> 259, K=7 -> 297. `recommended_k` = 12.
  - First upload of this JSON was accidentally the stale original 9-session baseline (strict-AND 6, n_sessions 9) -- caught because the numbers didn't match `compare_gap_vs_vanilla.py`'s already-known strict-AND-14/13-session result. Re-run against the correct `skip5_2` path confirmed the real number above.

### `MAX_GAP=6` run (`track2p_1-18gap3-skip5_gap6`) -- real gain at looser K

- Ran before pausing: `MAX_GAP=6` (vs. the day's `MAX_GAP=3`) gap-tolerant chaining on the same cleaned 13-session list.
- `compare_gap_vs_vanilla.py`: 442 rows gained sessions (up from 342 at `MAX_GAP=3`). Still 0/9 near-misses rescued, still all missing exactly `03-10-26` -- confirms again that's a hard structural limit, not something any `max_gap` fixes.
- `fix3_partial_tracks.py` K-curve vs. `MAX_GAP=3`: K=13/12/11 identical (14 / 45 / 102) -- rows missing only 1-2 sessions already had a gap small enough for `max_gap=3` to bridge, so a bigger cap adds nothing there. Real gains show up at K=10 and looser: K=10 158->161, K=9 206->222, K=8 259->295, K=7 297->353 (+19%).
- **Practical takeaway: use `skip5_gap6`'s output if downstream analysis can tolerate K=7-10 (54-77% of sessions); `skip5_2`'s `MAX_GAP=3` output is equally good (and cheaper to have produced) for K=11+.**

## Where to pick up tomorrow

1. Closing verification per the stopping rule from round 4/5: re-run `screen_sessions.py` + `registration_quality_scan.py` fresh on `skip5_2` (or `skip5_gap6`, same session list) to confirm nothing new shows up now that `12-16-25` is gone. Expect clean, but confirm rather than assume.
2. (Optional) `estimate_fix2_ceiling.py` on the final list -- worth checking now that it's clean whether fix #2 (anchor-agnostic seeding) looks more compelling at 13 sessions than earlier checkpoints.
3. Decide which `MAX_GAP` output (`skip5_2` vs. `skip5_gap6`) to actually hand off downstream, based on what K the representation-drift analysis can tolerate (see above).
4. Bigger-picture, still parked from 2026-07-20: **note -- "push to 18 sessions" is now stale, that checkpoint was reached and exclusion-cleaned this session; the real remaining item is testing generality on a second mouse** (and future sessions beyond 18, if the series continues); confirm GitHub push actually succeeded for both repos (never reconfirmed since the ownership-transfer issue on 2026-07-20); missing-data convention for rewriting suite2p output folders with fix #3's partial-track results still needs input from the `Drift` (representation-drift) codebase side.

---

## 2026-07-20

### Repo restructuring

- Split the old mixed `/Users/wehr/Documents/Analysis/Drift` folder into two separate projects, each its own git repo:
  - `/Users/wehr/Documents/Analysis/Drift` -- pure MATLAB representation-drift analysis (all the `.m` files, `driftlog.txt`, `PlotTrack2P_mw.*`)
  - `/Users/wehr/Documents/Analysis/track2p-tracking-fix` -- this python tooling
- Both git-initialized locally, `.gitignore` added (excludes `__pycache__`, `.DS_Store`, `*.bak`, `*.mat` in the fix repo), first commits made.
- GitHub: still being sorted out at pause time. The `Drift` repo hit a string of auth/ownership issues (SSH key setup, stale `known_hosts` entry from GitHub's documented 2023 RSA key rotation, repo created under the wrong owner). Last state: repo needed to be created/transferred to the `wehr-lab` org via GitHub's Transfer ownership feature. **Not confirmed complete** -- check `git remote -v` and try a push in both repos before assuming this is done. `track2p-tracking-fix`'s GitHub remote was never explicitly set up in this session at all.
- Known quirk: git commits run through Claude on these mounted folders sometimes leave stale lock files (`HEAD.lock`, `index.lock`) that Claude cannot delete (permission denied even for `rm`) -- if a `git commit` run through Claude fails with a lock error, clear it from Terminal directly (`rm -f .git/*.lock .git/objects/*.lock`) rather than expecting Claude to fix it.

### Workflow improvement: settings split

- Root problem: per-run launcher settings (`TRACK_OPS_CFG`, `ALL_DS_PATH`, `NEW_BASE_PATH`, `MAX_GAP`, `EXCLUDE_MATCH`, ...) used to live inside `run_gap_tolerant.py`/`run_exclude_session.py` themselves, so any code revision overwrote hand-edited values.
- Fixed by splitting each launcher into pure logic (`run_gap_tolerant.py`, `run_exclude_session.py`) + a settings file Claude never touches (`run_gap_tolerant_settings.py`, `run_exclude_session_settings.py`). Both launchers now wrapped in `if __name__ == '__main__':` (also required for the parallel-worker feature below to be safe on macOS).
- Added `load_all_ds_path(save_path)` to `session_order_utils.py` -- loads a previous run's session list without hand-typing paths, for chaining exclusion rounds. Distinct from `find_session_dirs()`, which is for scanning raw data only.

### Feature: parallel gap-pair precompute

- `run_gap_tolerant.py` on the raw 18-session dataset crashed with a native `malloc: Heap corruption detected` -- traced to elastix's compiled registration bindings accumulating state across many sequential calls in one process.
- Added checkpointing (`fix1_gap_tolerant_chain.py`): every gap registration is persisted to `gap_cache_checkpoint.npy` as it's computed, so a crash no longer loses prior work -- just rerun the same launcher and it resumes.
- Added `precompute_gap_pairs_parallel()` + `N_WORKERS` setting: dispatches gap registrations across worker processes (`concurrent.futures.ProcessPoolExecutor`, Python's closest equivalent to MATLAB's `parfor`). Caps ITK's internal thread count to 1 per worker to avoid oversubscription. **Untested against real elastix** -- validate on a small session subset before trusting it on a big run (was building toward using this on the 10-core iMac, or eventually the HPC cluster).

### Bug fix: `fix3_partial_tracks.py` recommended-K logic

- `recommended_k` was locking onto `K = n_sessions` (the strict-AND entry, always 0 cells whenever `strict_and_count == 0`) instead of ever considering looser K values -- the fallback condition was trivially true on the very first entry checked. Fixed to anchor to the loosest-K recovery count instead when there's no strict-AND baseline to double.

### New tool: `inspect_registration_pair.py`

- Built to resolve an ambiguity: `screen_sessions.py` flagged session 4 (`BAD_NEIGHBOR_TRANSITIONS`, neighbor rate 15.6% vs. 27-82% everywhere else), but its mean image and cell count looked completely normal in `export_session_qc.py`/`compare_session_qc.m`.
- Runs the actual `reg_img_elastix()` call your pipeline uses for one specific session pair, outputs a red/green overlay (misaligned = visible fringing, aligned = yellow/white). Confirmed striking, real misalignment for both the 3-4 and 4-5 pairs -- session 4 has a genuine registration/alignment problem invisible to a plain mean-image comparison.

### Analysis progress: wehr5336, 18-session dataset

- Round 1 exclusion: dropped `02-03-26` (low cell count) -> `track2p_1-18gap3-skip` (17 sessions).
- Round 2 exclusion: dropped `02-24-26` (blur/registration quality) -> `track2p_1-18gap3-skip2` (16 sessions, vanilla/`MAX_GAP=1`).
- Ran gap-tolerant chaining (`MAX_GAP=3`) on the 16-session list -> `track2p_1-18gap3-skip2_2`.
  - Strict-AND: 0 for both vanilla and gap-tolerant (expected -- mathematically guaranteed identical, not a bug).
  - `compare_gap_vs_vanilla.py`: 227/1256 rows improved under gap-tolerant, 0 rows fully rescued to completion -- dropout at 16 sessions is spread across many transitions per row rather than concentrated at one bridgeable spot (unlike the 7-9 session case).
  - `fix3_partial_tracks.py` recovery curve: gap-tolerant massively outperforms vanilla at every K (103 vs. 1 cells at K=8; 69 vs. 1 at K=11) -- confirms the fix pipeline's benefit generalizes to 16 sessions, just shows up as partial-recovery-curve improvement rather than full-completion rescues.
- `missing_session_histogram.py` (now revised to show calendar dates) flagged session 4 (`12-09-25`) as dominant-missing (71.1%) with a sharp, isolated spike unlike the gradual distance-decay pattern at the tail (sessions 11-15).
  - `screen_sessions.py` confirmed: session 4 flagged `BAD_NEIGHBOR_TRANSITIONS` + `DOMINANT_MISSING_SESSION`; session 5 and session 15 flagged `DOMINANT_MISSING_SESSION` only.
  - Visually confirmed via `inspect_registration_pair.py`: real registration failure at session 4.
  - Decided: exclude session 4 only. Session 5's flag pattern (missing `BAD_NEIGHBOR_TRANSITIONS`, i.e. its 5-6 pairing is fine) points to fallout from session 4 breaking chains at the 4-5 transition, not an independent problem. Session 15 is the last session in the list, so its flag is more likely ordinary distance-decay than a real data problem. Neither is being excluded for now.
- `run_exclude_session_settings.py` is already configured for round 3 (confirmed current values):
  ```python
  ALL_DS_PATH = load_all_ds_path(
      '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip2/track2p'
  )
  NEW_BASE_PATH = '/Users/wehr/Documents/Projects/Representational drift/wehr5336/track2p_1-18gap3-skip3'
  EXCLUDE_MATCH = '12-09-25'
  ```
  **Not yet run.**

## Where to pick up tomorrow

1. Run `python run_exclude_session.py` (settings already set, see above) to produce `track2p_1-18gap3-skip3` -- the 15-session vanilla list with session 4 dropped.
2. Sanity-check with `screen_sessions.py` on skip3 before moving on.
3. Update `run_gap_tolerant_settings.py`: `ALL_DS_PATH = load_all_ds_path('.../track2p_1-18gap3-skip3/track2p')`, new `NEW_BASE_PATH` (e.g. `..._skip3_2`), then run `run_gap_tolerant.py` (`MAX_GAP=3`) to get the gap-tolerant version of the cleaned 15-session list.
4. Re-run `compare_gap_vs_vanilla.py`, `missing_session_histogram.py`, and `fix3_partial_tracks.py` on the new output -- check whether the recovery curve improves and whether a new dominant session emerges once session 4's fallout clears.
5. Loop steps 1-4 of `track2p_fix_workflow.md` (screen -> confirm -> exclude -> re-screen) until clean or diminishing returns.

### Parked / not urgent, but don't forget

- Confirm GitHub push actually succeeded for both repos (ownership transfer status unconfirmed for `Drift`; `track2p-tracking-fix` remote never set up in this session at all).
- `N_WORKERS` parallel gap precompute is implemented but never validated against real elastix -- worth a small-scale test on the iMac before relying on it for a big run, and before the eventual HPC push.
- `estimate_fix2_ceiling.py` hasn't been re-run at the larger session counts -- worth checking whether fix #2 (anchor-agnostic seeding) looks more compelling at 15-16 sessions than it did at 9.
- Eventually: push on to the full 18 sessions and a second mouse for generality testing (original plan, paused to chase down the session-4 issue first).
- Still unresolved from earlier: missing-data convention for rewriting suite2p output folders with fix #3's partial-track results -- needs input from the downstream longitudinal-analysis codebase (the `Drift`/representation-drift repo), not yet addressed.
