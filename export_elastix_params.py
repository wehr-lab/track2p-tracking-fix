"""
export_elastix_params.py

One-time (or "whenever track_ops.transform_type changes") export of an
elastix parameter FILE that a standalone `elastix` command-line install can
use directly -- for preflight_registration_check.m, which runs entirely in
MATLAB and shells out to the elastix CLI rather than calling into Python at
check-time (no MATLAB<->Python bridge, no Python env needed on the rig
computer for the routine, every-session check).

IMPORTANT CAVEAT -- read before trusting this for real decisions: this
script does NOT introspect track2p's actual reg_img_elastix() function (its
source wasn't available in the environment this was written in). Instead it
asks SimpleElastix for its own STANDARD default parameter map for your
transform_type (sitk.GetDefaultParameterMap) -- a reasonable, well-tested
starting point (it's what most SimpleElastix-based pipelines are built on
top of in the first place), but not a verified match to whatever specific
metric/optimizer/resolution settings reg_img_elastix.py actually uses. This
matters for MATLAB fully predicting what the real Python pipeline will do:
if the two run different Elastix parameters, the two are still both real,
literal elastix registrations (not toy approximations), but potentially
different ONES. Before trusting the preflight check to predict the real
pipeline's outcome, diff the file this produces against reg_img_elastix.py
(or whatever parameter map it constructs) once by hand and adjust
elastix_params.txt directly if anything differs -- MATLAB just reads that
text file, so hand edits stick without touching this export script again.

Usage:
    python export_elastix_params.py --cfg track2p_settings.cfg elastix_params.txt
    python export_elastix_params.py --transform-type affine elastix_params.txt   # skip the .cfg entirely

Requires SimpleITK built with the Elastix extension (the `SimpleITK-SimpleElastix`
PyPI package, not plain `SimpleITK` -- plain SimpleITK doesn't have
GetDefaultParameterMap/WriteParameterFile). Confirm with:
    python -c "import SimpleITK as sitk; sitk.GetDefaultParameterMap('affine')"
"""

import argparse
import SimpleITK as sitk


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('out_path', help='output elastix parameter .txt file')
    parser.add_argument('--cfg', default=None,
                         help='track2p_settings.cfg to read transform_type from (see track_ops_config.py)')
    parser.add_argument('--transform-type', default=None,
                         help='elastix transform type directly (e.g. translation/rigid/affine/bspline) -- '
                              'use this instead of --cfg if you don\'t have a .cfg file handy')
    args = parser.parse_args()

    if args.cfg is not None:
        from track_ops_config import load_track_ops
        track_ops = load_track_ops(args.cfg)
        transform_type = track_ops.transform_type
        print(f'Read transform_type={transform_type!r} from {args.cfg}')
    elif args.transform_type is not None:
        transform_type = args.transform_type
    else:
        raise ValueError('Pass either --cfg track2p_settings.cfg or --transform-type <type>')

    # SimpleElastix's own transform-type vocabulary is lowercase
    # ('translation', 'rigid', 'affine', 'bspline') -- normalize in case
    # track_ops stores it capitalized or in track2p's own convention differs.
    sitk_transform_type = transform_type.lower()
    try:
        param_map = sitk.GetDefaultParameterMap(sitk_transform_type)
    except RuntimeError as e:
        raise ValueError(
            f"SimpleElastix doesn't recognize transform_type={transform_type!r} "
            f"(tried {sitk_transform_type!r}). Expected one of: translation, rigid, affine, bspline. "
            f"If track_ops.transform_type uses different wording, pass --transform-type explicitly "
            f"with one of those instead."
        ) from e

    # Explicit, so the MATLAB side always knows exactly what file name/format
    # to read back after calling the elastix CLI, regardless of the default.
    param_map['ResultImageFormat'] = ['mhd']
    param_map['ResultImagePixelType'] = ['float']

    sitk.WriteParameterFile(param_map, args.out_path)
    print(f'Wrote {args.out_path} (transform_type={transform_type})')
    print('\nThis is SimpleElastix\'s STANDARD default parameter map for this transform type -- see the')
    print('module docstring\'s caveat. Diff it against what reg_img_elastix.py actually uses once, by hand,')
    print('before trusting preflight_registration_check.m to predict the real pipeline\'s behavior.')


if __name__ == '__main__':
    main()
