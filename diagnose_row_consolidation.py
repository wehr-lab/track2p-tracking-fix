"""
diagnose_row_consolidation.py

Run this LOCALLY against a PRE-fix and a POST-fix track2p output folder for
the SAME mouse and SAME max_gap (the plane{j}_match_mat.npy files are small;
no need to upload the large suite2p/NAS data to Claude).

MOTIVATION (see Drift repo's SESSION_LOG.md, 2026-07-29/2026-08-03 addenda):
fixing init_all_pl_match_mat's ghost-row bug (track2p/match/utils.py, in the
external track2p fork) made wehr5149's candidate pool SHRINK (2194 -> 1998,
gap3-pre vs gap3) while every fix3_partial_tracks.py K-bucket went UP. That's
the opposite direction from wehr5336, where the pool grew. Working
hypothesis: pre-fix, a session-0 cell that failed the naive session0->1
transition was invisible at session 0 (all-None row, per the ghost-row bug)
-- but if add_anchor_agnostic_chains (fix #2) later picked up that SAME
physical cell from some other session as an anchor, it would create a
SEPARATE new row for it, double-counting one physical cell as two partial
rows. Post-fix, that cell already has a valid session-0-anchored row (my fix
guarantees pl_match_mat[:,0] = arange(n0) unconditionally), so fix #2 no
longer needs to (and should not) create the duplicate -- fewer total rows,
but more of them fully/correctly populated.

This script tests that directly: for every row in the PRE-fix file that is
one of fix #2's anchor-agnostic additions (structurally, that's every row
at index >= n0, since add_anchor_agnostic_chains appends new rows via
np.vstack AFTER fix #1's original session-0-anchored block -- see that
function's own docstring in fix1_gap_tolerant_chain.py), check whether its
non-None (session, raw-ROI-index) entries are consistent with exactly one
row in the POST-fix file's session-0-anchored block (rows 0..n0-1). If most
of them are, that's direct evidence for the consolidation hypothesis above
rather than just a coincidental pool-size change.

n0 (the size of the original session-0-anchored block) is inferred from the
POST-fix file itself: since the fix guarantees pl_match_mat[:,0] ==
arange(n0) for exactly the first n0 rows and None at column 0 for every
anchor-agnostic row (a fix #2 row's anchor session is never session 0 -- if
it were, fix #1 would already have claimed it), n0 = count of rows with a
non-None column-0 entry. The script sanity-checks this assumption before
using it (see the printed invariant checks) and aborts with an explanation
if it doesn't hold -- e.g. if you pass --post a file that wasn't actually
produced by the fixed code.

Usage:
    python diagnose_row_consolidation.py --pre /path/to/pre_fix/track2p \\
                                          --post /path/to/post_fix/track2p \\
                                          --plane 0

Prints a summary; does not modify anything.
"""

import sys
import os
import argparse
import numpy as np
from collections import Counter


def load_match_mat(save_path, plane):
    match_mat_path = os.path.join(save_path, f'plane{plane}_match_mat.npy')
    if not os.path.exists(match_mat_path):
        sys.exit(f'Not found: {match_mat_path}')
    return np.load(match_mat_path, allow_pickle=True), match_mat_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pre', required=True,
                         help='track2p save_path for the PRE-fix run')
    parser.add_argument('--post', required=True,
                         help='track2p save_path for the POST-fix run')
    parser.add_argument('--plane', type=int, default=0)
    args = parser.parse_args()

    pre_mat, pre_path = load_match_mat(args.pre, args.plane)
    post_mat, post_path = load_match_mat(args.post, args.plane)

    n_pre, n_sess_pre = pre_mat.shape
    n_post, n_sess_post = post_mat.shape

    print(f'PRE  : {pre_path}  -> {n_pre} rows x {n_sess_pre} sessions')
    print(f'POST : {post_path} -> {n_post} rows x {n_sess_post} sessions')

    if n_sess_pre != n_sess_post:
        sys.exit(f'\nERROR: session count differs ({n_sess_pre} vs '
                  f'{n_sess_post}) -- these are not comparable runs '
                  f'(different session list/max_gap?). Aborting.')

    n_sess = n_sess_pre

    # -----------------------------------------------------------------
    # Infer n0 from the POST-fix file, and sanity-check the invariant
    # the fix is supposed to guarantee.
    # -----------------------------------------------------------------

    post_col0_present = np.array(
        [post_mat[i, 0] is not None for i in range(n_post)])
    n0 = int(post_col0_present.sum())

    print(f'\nInferred n0 (session-0-anchored block size, from POST file) '
          f': {n0}')

    # invariant: for those n0 rows, is post_mat[row,0] == row? (this is
    # exactly what the fix does: pl_match_mat[:,0] = arange(n0))
    self_identity_ok = all(
        post_mat[i, 0] == i for i in range(n0)
        if post_col0_present[i])
    contiguous_at_head = np.array_equal(
        np.where(post_col0_present)[0], np.arange(n0))

    print(f'  self-identity (post_mat[i,0]==i for i<n0)? {self_identity_ok}')
    print(f'  session-0 block is rows 0..{n0-1} contiguously? '
          f'{contiguous_at_head}')

    if not (self_identity_ok and contiguous_at_head):
        sys.exit(
            '\nERROR: POST file does not look like it was produced by the '
            'fixed init_all_pl_match_mat (self-identity/contiguity '
            'invariant failed) -- the n0 inference above is not valid for '
            'this file. Aborting rather than reporting a misleading '
            'comparison.')

    if n0 > n_pre:
        sys.exit(
            f'\nERROR: inferred n0 ({n0}) exceeds the PRE file\'s total row '
            f'count ({n_pre}) -- PRE and POST are not comparable runs '
            f'(different session-0 iscell count?). Aborting.')

    # -----------------------------------------------------------------
    # Sanity check on the PRE side: within its own first n0 rows, wherever
    # column 0 IS filled, does it also show self-identity? (partial check
    # that PRE's first n0 rows really are the same session-0-anchored
    # block, positionally aligned with POST's.)
    # -----------------------------------------------------------------

    pre_head_col0_filled = [
        (i, pre_mat[i, 0]) for i in range(n0) if pre_mat[i, 0] is not None]
    pre_head_self_identity_ok = all(v == i for i, v in pre_head_col0_filled)

    print(f'\nPRE file, rows 0..{n0-1}: {len(pre_head_col0_filled)}/{n0} '
          f'have column 0 filled (expected: fewer than n0, that\'s the '
          f'ghost-row bug -- the rest were left None pre-fix)')
    print(f'  of those, self-identity holds (value==row)? '
          f'{pre_head_self_identity_ok} (expected True -- confirms PRE\'s '
          f'first n0 rows are positionally the same session-0-anchored '
          f'block as POST\'s)')

    # -----------------------------------------------------------------
    # Identify PRE's fix #2 (anchor-agnostic) rows: everything at index
    # >= n0, structurally guaranteed by add_anchor_agnostic_chains's
    # np.vstack(...) append happening strictly after fix #1's original
    # session-0-anchored block.
    # -----------------------------------------------------------------

    n_pre_fix2_rows = n_pre - n0
    n_post_fix2_rows = n_post - n0

    print(f'\nFix #2 (anchor-agnostic) row counts:')
    print(f'  PRE  : {n_pre_fix2_rows}')
    print(f'  POST : {n_post_fix2_rows}')

    if n_pre_fix2_rows <= 0:
        print('\nNo PRE-side anchor-agnostic rows to check. Done.')
        return

    # -----------------------------------------------------------------
    # Build reverse lookup: for each session column c, map
    # raw_roi_index -> post-fix session-0-block row index, using ONLY
    # rows 0..n0-1 of the POST file.
    # -----------------------------------------------------------------

    col_value_to_post_row = [dict() for _ in range(n_sess)]

    for c in range(n_sess):
        d = col_value_to_post_row[c]
        for row in range(n0):
            v = post_mat[row, c]
            if v is not None:
                d[v] = row

    # -----------------------------------------------------------------
    # For each PRE fix#2 row, gather every post-fix session-0-block row
    # implicated by its non-None entries, and classify.
    # -----------------------------------------------------------------

    consolidated = []       # (pre_row, post_row, n_columns_agreeing)
    ambiguous = []          # (pre_row, {post_row: count})
    not_found = []          # pre_row

    for pre_row in range(n0, n_pre):
        implicated = Counter()
        n_entries = 0
        for c in range(n_sess):
            v = pre_mat[pre_row, c]
            if v is None:
                continue
            n_entries += 1
            post_row = col_value_to_post_row[c].get(v)
            if post_row is not None:
                implicated[post_row] += 1

        if n_entries == 0:
            # shouldn't happen (min_chain_length=2 discards these before
            # they're ever written) but guard anyway
            not_found.append(pre_row)
            continue

        if not implicated:
            not_found.append(pre_row)
        elif len(implicated) == 1:
            (post_row, n_agree), = implicated.items()
            consolidated.append((pre_row, post_row, n_agree))
        else:
            ambiguous.append((pre_row, dict(implicated)))

    n_checked = n_pre_fix2_rows

    print(f'\n=== Results: {n_checked} PRE-fix anchor-agnostic rows ===')
    print(f'  Consolidated into a single POST session-0-block row : '
          f'{len(consolidated)} ({100*len(consolidated)/n_checked:.1f}%)')
    print(f'  Ambiguous (implicate >1 different POST rows)        : '
          f'{len(ambiguous)} ({100*len(ambiguous)/n_checked:.1f}%)')
    print(f'  Not found in POST session-0 block (still separate,  '
          f'or lost/changed) : {len(not_found)} '
          f'({100*len(not_found)/n_checked:.1f}%)')

    if consolidated:
        print(f'\nFirst 10 consolidated examples '
              f'(pre_row -> post_row, columns agreeing):')
        for pre_row, post_row, n_agree in consolidated[:10]:
            print(f'  {pre_row} -> {post_row}  ({n_agree} column(s) agree)')

    if ambiguous:
        print(f'\nFirst 5 ambiguous examples (pre_row -> {{post_row: count}}):')
        for pre_row, counts in ambiguous[:5]:
            print(f'  {pre_row} -> {counts}')

    print()
    if len(consolidated) / n_checked > 0.5:
        print('CONCLUSION: a majority of PRE-fix anchor-agnostic rows are '
              'consistent with a single POST-fix session-0-anchored row -- '
              'supports the consolidation hypothesis (these were '
              'previously-duplicated cells, now correctly represented '
              'once, which explains why the total candidate pool shrank '
              'while every K-bucket in fix3_partial_tracks.py grew).')
    else:
        print('CONCLUSION: consolidation does NOT explain most of the '
              'PRE-fix anchor-agnostic rows -- the pool-size decrease is '
              'likely coming from somewhere else (e.g. fix #2\'s own '
              'candidate set changing between runs for unrelated reasons). '
              'Worth checking the "not found"/"ambiguous" rows directly.')


if __name__ == '__main__':
    main()
