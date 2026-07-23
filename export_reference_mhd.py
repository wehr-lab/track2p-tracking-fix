"""
export_reference_mhd.py

One-time export of a single suite2p session's mean image straight to
MetaImage (.mhd/.raw) format, for preflight_registration_check.m to read
directly as its reference image.

Replaces the export_session_qc.py -> session_qc.mat -> MATLAB load() ->
MATLAB writeMHD() chain with a single step: this script writes the
.mhd/.raw pair itself, and preflight_registration_check.m reads it back
with the SAME readMHD() it already uses to parse elastix's own output --
no MATLAB-side .mat handling needed at all, and no track2p run required
either (this reads straight from suite2p's own ops.npy, via the same
load_mean_img() helper registration_quality_scan.py and
inspect_registration_pair.py already use -- not from any track2p output).

Run once, whenever you pick a new reference session (not before every
preflight check):

    python export_reference_mhd.py /path/to/session_dir --plane 0 --out ref

Writes ref.mhd + ref.raw (or wherever --out points). Point
preflight_registration_check.m's REFERENCE_MHD_BASE config at the same
base path (no extension).

FORMAT NOTE: ElementType=MET_FLOAT, DimSize = ncols nrows (x, y -- MHD's
axis order), raw bytes written row-major/x-fastest via numpy's native
C-contiguous .tofile() -- no transpose needed here, unlike MATLAB's own
writeMHD(), which needs one specifically because MATLAB arrays are
column-major internally. Both write paths were validated (independently,
via Python/SimpleITK round-trip tests) to produce byte-identical layout
against real elastix output -- see preflight_registration_check.m's
top-of-file notes.
"""

import os
import argparse
import numpy as np
from registration_qc_utils import load_mean_img


def write_mhd(img, out_base):
    img = np.asarray(img, dtype=np.float32)
    n_rows, n_cols = img.shape
    raw_name = os.path.basename(out_base) + '.raw'
    out_dir = os.path.dirname(out_base) or '.'
    os.makedirs(out_dir, exist_ok=True)

    # C-order (numpy's default, and what .tofile() writes) is already
    # row-major/x-fastest -- exactly MHD's raw convention -- so no
    # transpose is needed here (contrast with the MATLAB-side writeMHD(),
    # which transposes first because MATLAB stores column-major).
    img.tofile(os.path.join(out_dir, raw_name))

    with open(out_base + '.mhd', 'w') as f:
        f.write('ObjectType = Image\n')
        f.write('NDims = 2\n')
        f.write(f'DimSize = {n_cols} {n_rows}\n')  # MHD DimSize is (x, y) = (ncols, nrows)
        f.write('ElementType = MET_FLOAT\n')
        f.write('ElementByteOrderMSB = False\n')
        f.write('ElementSpacing = 1 1\n')
        f.write(f'ElementDataFile = {raw_name}\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('session_dir', help='raw session folder containing suite2p/plane{j}/ops.npy '
                                             '(e.g. one entry from find_session_dirs() / all_ds_path)')
    parser.add_argument('--plane', type=int, default=0)
    parser.add_argument('--out', default='ref', help='output base path, no extension '
                                                       '(writes <out>.mhd + <out>.raw)')
    args = parser.parse_args()

    img = load_mean_img(args.session_dir, args.plane)
    write_mhd(img, args.out)
    print(f'Wrote {args.out}.mhd + {args.out}.raw from {args.session_dir} '
          f'(plane {args.plane}, {img.shape[0]}x{img.shape[1]})')
    print("Point preflight_registration_check.m's REFERENCE_MHD_BASE at this same base path.")


if __name__ == '__main__':
    main()
