#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# datview/scripts/h5fields.py
# Show which datasets in an HDF5 / EIGER master file can become datgen.py columns:
# scalars (same value for every frame) and arrays with one value per frame.

import argparse

try:
    import hdf5plugin  # noqa: F401
except ImportError:
    pass
import h5py
import numpy as np


def main():
    p = argparse.ArgumentParser(description='List candidate per-frame metadata in an HDF5 file for datgen.py --h5. '
                                            'Frame data (/entry/data) is not opened.')
    p.add_argument('file')
    p.add_argument('--nframes', type=int, default=None,
                   help='number of frames; arrays of this length are flagged as per-frame (default: from detectorSpecific/nimages)')
    a = p.parse_args()

    rows = []
    with h5py.File(a.file, 'r') as f:
        n = a.nframes
        ni = f.get('/entry/instrument/detector/detectorSpecific/nimages')
        if n is None and ni is not None:
            n = int(ni[()])

        def visit(name, obj):
            if name.startswith('entry/data'):
                return
            if isinstance(obj, h5py.Dataset) and obj.dtype.kind in 'iufb' and obj.size <= 10**6:
                shape = obj.shape
                if shape == () or obj.size == 1:
                    kind, example = 'scalar', np.ravel(obj[()])[0]
                elif len(shape) == 1:
                    kind = 'per-frame' if n and shape[0] == n else 'array[%d]' % shape[0]
                    example = obj[0]
                else:
                    return
                rows.append(('/' + name, kind, example))
        f.visititems(visit)

    print('frames: %s' % (n if n is not None else 'unknown (use --nframes)'))
    w = max([len(r[0]) for r in rows] + [10])
    for path, kind, ex in rows:
        print('%-*s  %-12s  %s' % (w, path, kind, ex))
    print('\nUse with: datgen.py --h5 <path> [--h5 <path> ...]  (per-frame arrays are indexed by event)')


if __name__ == '__main__':
    main()
