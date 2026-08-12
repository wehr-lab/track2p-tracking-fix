"""
screen_and_scan.py

Convenience wrapper: runs screen_sessions.py then registration_quality_scan.py
back to back against the same save_path/plane. These two are basically always
run together in practice -- screen_sessions.py's intrinsic (cell count,
sharpness) and relational (neighbor match rate, dominant-missing-session)
per-SESSION triage, followed by registration_quality_scan.py's per-PAIR SSIM
scan + IOU histogram grid -- so this just saves the two separate invocations
(and remembering to point both at the same save_path/plane).

Calls each script's main() in-process (not via subprocess), so both share
this process's already-imported track2p clone path etc. Each tool's own
flags are still exposed here, just disambiguated where both scripts happen
to define a same-named flag for a different purpose (screen_sessions.py's
--z-thresh flags cell count / sharpness outliers vs. the group; registration_
quality_scan.py's --z-thresh flags SSIM outliers vs. the group -- unrelated
thresholds that happen to share a name in their own standalone CLIs) --
--screen-z-thresh / --scan-z-thresh here.

Usage:
    python screen_and_scan.py /path/to/track2p/save_path
    python screen_and_scan.py /path/to/track2p/save_path --plane 0
    python screen_and_scan.py /path/to/track2p/save_path --scan-z-thresh 2.0 --ssim-floor 0.3
    python screen_and_scan.py /path/to/track2p/save_path --no-grid   # table-only registration scan

Run each script standalone (screen_sessions.py / registration_quality_scan.py)
if you only want one of the two, or want to point them at different
save_paths/planes.
"""

import argparse

import screen_sessions
import registration_quality_scan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('save_path', help='track2p save_path (shared by both tools)')
    parser.add_argument('--plane', type=int, default=0)

    screen_group = parser.add_argument_group('screen_sessions.py options')
    screen_group.add_argument('--screen-z-thresh', type=float, default=2.5,
                               help='robust z-score magnitude to flag cell count / sharpness '
                                    'as an outlier (default 2.5)')
    screen_group.add_argument('--match-rate-mads', type=float, default=1.5,
                               help='flag a session if ALL its neighboring pairs are this many '
                                    'MADs below the median pair match rate (default 1.5)')

    scan_group = parser.add_argument_group('registration_quality_scan.py options')
    scan_group.add_argument('--scan-z-thresh', type=float, default=2.0,
                             help='robust z-score magnitude to flag a pair\'s SSIM as an outlier (default 2.0)')
    scan_group.add_argument('--ssim-floor', type=float, default=0.3,
                             help='absolute SSIM floor -- flags a pair regardless of z-score (default 0.3)')
    scan_group.add_argument('--no-grid', action='store_true',
                             help='skip building the grid PNG, just print the table')
    scan_group.add_argument('--no-phase-corr-check', action='store_true',
                             help='skip the phase-correlation capture-range follow-up check on flagged pairs')
    scan_group.add_argument('--middle-panel', choices=['mov_raw', 'mov_reg'], default='mov_raw',
                             help='which mov image to show in the grid\'s middle column (default mov_raw)')
    scan_group.add_argument('--grid-out', default=None,
                             help='output PNG path for the grid (default: <save_path>/diagnostics/'
                                  'registration_quality_grid.png)')
    scan_group.add_argument('--panel-size', type=float, default=3.2, help='inches per panel (default 3.2)')
    scan_group.add_argument('--dpi', type=int, default=100, help='grid PNG dpi (default 100)')

    args = parser.parse_args(argv)

    print('=' * 70)
    print('STEP 1/2: screen_sessions.py -- per-session triage')
    print('=' * 70)
    screen_argv = [
        args.save_path,
        '--plane', str(args.plane),
        '--z-thresh', str(args.screen_z_thresh),
        '--match-rate-mads', str(args.match_rate_mads),
    ]
    screen_sessions.main(screen_argv)

    print('\n' + '=' * 70)
    print('STEP 2/2: registration_quality_scan.py -- per-pair SSIM scan + IOU histogram grid')
    print('=' * 70)
    scan_argv = [
        args.save_path,
        '--plane', str(args.plane),
        '--z-thresh', str(args.scan_z_thresh),
        '--ssim-floor', str(args.ssim_floor),
        '--middle-panel', args.middle_panel,
        '--panel-size', str(args.panel_size),
        '--dpi', str(args.dpi),
    ]
    if args.no_grid:
        scan_argv.append('--no-grid')
    if args.no_phase_corr_check:
        scan_argv.append('--no-phase-corr-check')
    if args.grid_out is not None:
        scan_argv += ['--grid-out', args.grid_out]
    registration_quality_scan.main(scan_argv)


if __name__ == '__main__':
    main()
