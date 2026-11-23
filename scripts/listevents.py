#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# datview/scripts/listevents.py
# Write a CrystFEL list ("file //event" per frame) for multi-frame HDF5 files, for datgen.py
# list mode. EIGER master files are handled by counting frames across their data_NNNNNN links.
# CrystFEL's own list_events does the same using a geometry file.

import argparse
import os
import sys

try:
    import hdf5plugin  # noqa: F401
except ImportError:
    pass
import h5py


def count_frames(path, h5path=None):
    with h5py.File(path, 'r') as f:
        if h5path:
            return f[h5path].shape[0]
        if isinstance(f.get('/entry/data/data'), h5py.Dataset):
            return f['/entry/data/data'].shape[0]
        entry = f.get('/entry/data')
        if entry is not None:
            n = 0
            for k in sorted(entry.keys()):
                if k.startswith('data_'):
                    try:
                        n += entry[k].shape[0]
                    except KeyError:
                        print("listevents: %s: linked file for %s missing" % (path, k), file=sys.stderr)
            if n:
                return n
        if 'nimages' in f.get('/entry/instrument/detector/detectorSpecific', {}):
            return int(f['/entry/instrument/detector/detectorSpecific/nimages'][()])
    raise SystemExit("listevents: cannot find frames in %s; give --h5path" % path)


def main():
    p = argparse.ArgumentParser(description='List the frames of multi-frame HDF5 files (EIGER master, CXI, ...) as "file //event" lines.')
    p.add_argument('--h5path', default=None, help='dataset holding the frames (default: /entry/data/data, or the EIGER data_NNNNNN links)')
    p.add_argument('-o', '--outname', required=True, help='output list file')
    p.add_argument('files', nargs='+', help='HDF5 files (for EIGER, the *_master.h5 files)')
    a = p.parse_args()
    with open(a.outname, 'w') as out:
        for fn in a.files:
            path = os.path.abspath(fn)
            for i in range(count_frames(path, a.h5path)):
                out.write('%s //%i\n' % (path, i))


if __name__ == '__main__':
    main()
