#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# datview/datgen.py
# Data reduction script: CrystFEL stream(s) or list file(s) -> tab-separated .dat table.
# Author Natasha Stander. 2026 update: CrystFEL 0.10-0.12 stream fields, exact byte offsets,
# stream header capture, HDF5/EIGER master metadata, sample position mapping.

import os
import sys
import argparse
from math import sqrt, cos, radians

import numpy as np

from api.groupmgr import GroupMgr
from api import positions as posmod

try:
    import hdf5plugin  # noqa: F401  registers bitshuffle/LZ4 filters used by EIGER files
except ImportError:
    pass
import h5py

try:
    _trapezoid = np.trapezoid
except AttributeError:          # numpy < 2.0
    _trapezoid = np.trapz

import re

# ---------------------------------------------------------------------------
# Stream markers (as written by libcrystfel/src/stream.c)
# ---------------------------------------------------------------------------
CHUNK_START = b"----- Begin chunk -----"
CHUNK_END = b"----- End chunk -----"
PEAKS_START = b"Peaks from peak search"
PEAKS_END = b"End of peak list"
CRYSTAL_START = b"--- Begin crystal"
CRYSTAL_END = b"--- End crystal"
REFL_START = b"Reflections measured after indexing"
REFL_END = b"End of reflections"

# Cheetah-style file names (LCLS legacy): experiment-r0123-class1-c00.cxi
CHEETAH_CLASS = re.compile(r'.*r\d{4}(?:-class(\d))?(?:-c(\d{2}))?\.(?:cxi|h5)')
CHEETAH_RUN = re.compile(r'.*r(\d{4})')

# Standard NeXus/NXmx paths written by DECTRIS EIGER detectors (used by --eiger)
EIGER_FIELDS = ['/entry/instrument/detector/detector_distance',
                '/entry/instrument/detector/beam_center_x',
                '/entry/instrument/detector/beam_center_y',
                '/entry/instrument/detector/count_time',
                '/entry/instrument/beam/incident_wavelength',
                '/entry/sample/goniometer/omega']


def _first(v):
    return v.split()[0]


# ---------------------------------------------------------------------------
# Line parsers: line prefix -> function(value_text, record)
# ---------------------------------------------------------------------------
def _cell(v, cur):
    p = v.replace(',', ' ').split()
    cur['a'], cur['b'], cur['c'] = p[0], p[1], p[2]
    cur['alpha'], cur['beta'], cur['gamma'] = p[4], p[5], p[6]


def _vec(names):
    def f(v, cur):
        p = v.split()
        for n, x in zip(names, p[:3]):
            cur[n] = x
    return f


def _detshift(v, cur):
    # "x = 0.081 y = 0.065 mm"
    p = v.split()
    cur['detdx'], cur['detdy'] = p[2], p[5]


def _event(v, cur):
    # Event IDs look like "//12" (frame 12) or "//" (single frame file). Only a single
    # integer index is stored; other forms leave event = -1 and keep the raw text.
    cur['evstr'] = v
    idx = v.split('//', 1)[-1]
    cur['event'] = idx if idx.isdigit() else None


def _ifile(v, cur):
    cur['ifile'] = v
    m = CHEETAH_CLASS.search(v)
    if m:
        cur['class'], cur['subcxi'] = m.groups()
    m = CHEETAH_RUN.search(v)
    if m:
        cur['run'] = m.group(1)


def _set(name, conv=None):
    def f(v, cur):
        cur[name] = conv(v) if conv else v
    return f


CHUNK_FIELDS = [
    ('Image filename: ', _ifile),
    ('Event: ', _event),
    ('Image serial number: ', _set('serial')),
    ('hit = ', _set('hit')),
    ('indexed_by = ', _set('indby')),
    ('n_indexing_tries = ', _set('ntries')),
    ('photon_energy_eV = ', _set('phoen')),
    ('beam_divergence = ', _set('bmdv', _first)),
    ('beam_bandwidth = ', _set('bmbw', _first)),
    ('average_camera_length = ', _set('aclen', _first)),
    ('num_peaks = ', _set('npeak')),
    ('peak_resolution = ', _set('pkres', _first)),
]

CRYSTAL_FIELDS = [
    ('Cell parameters ', _cell),
    ('astar = ', _vec(('o1', 'o2', 'o3'))),
    ('bstar = ', _vec(('o4', 'o5', 'o6'))),
    ('cstar = ', _vec(('o7', 'o8', 'o9'))),
    ('lattice_type = ', _set('ltype')),
    ('centering = ', _set('cent')),
    ('unique_axis = ', _set('uaxis')),
    ('profile_radius = ', _set('prorad', _first)),
    ('predict_refine/final_residual = ', _set('presid', _first)),
    ('predict_refine/det_shift ', _detshift),
    ('diffraction_resolution_limit = ', _set('reslim', _first)),
    ('num_reflections = ', _set('nref')),
    ('num_saturated_reflections = ', _set('nsref')),
    ('num_implausible_reflections = ', _set('niref')),
]


class DatGenerator:
    # Column order of the 2019 version is kept (column numbers in the manual stay valid);
    # fields added for CrystFEL 0.10+ are appended.
    allstrcols = ['ifile', 'run', 'class', 'subcxi', 'event', 'indby', 'phoen', 'bmdv', 'bmbw', 'aclen',
                  'npeak', 'ltype', 'cent', 'a', 'b', 'c', 'alpha', 'beta', 'gamma', 'prorad', 'detdx',
                  'detdy', 'reslim', 'nref', 'nsref', 'niref', 'o1', 'o2', 'o3', 'o4', 'o5', 'o6', 'o7',
                  'o8', 'o9']
    internalcols = ['sfile', 'istart', 'iend', 'cstart', 'cend', 'pstart', 'pend', 'rstart', 'rend',
                    'multiid', 'multi', 'vol']
    newcols = ['serial', 'hit', 'ntries', 'pkres', 'uaxis', 'presid']
    allstreamcols = allstrcols + internalcols + newcols
    alllistcols = ['ifile', 'run', 'class', 'subcxi', 'event', 'basename']
    concatjoiner = "--"

    def __init__(self, out, streamcols, cxicols, staticlist, integratecols, concatint, groupmgr=None):
        cxicols = list(cxicols) + list(integratecols)
        self.cols = list(streamcols) + cxicols
        self.cxicols = cxicols
        self.integratecols = integratecols
        self.out = out
        self.groupmgr = groupmgr
        self.npdata = None
        self.npcols = None
        self.npsynccols = None
        self.groupcols = []
        self.static = dict(staticlist)
        self.concatint = concatint
        self.headercols = []
        self.positions = None
        self.h5 = H5Fields(cxicols, integratecols)
        for s in staticlist:
            self.cols.append(s[0])
        for row in concatint:
            self.cols.append(DatGenerator.concatjoiner.join(row))
        self.groupmgr_pending = groupmgr

    def finishColumns(self):
        """Group columns are resolved last so they can match on any other column."""
        groupmgr = self.groupmgr_pending
        if groupmgr is not None:
            for col in sorted(groupmgr.groups()):
                if col in self.cols:
                    self.cols[self.cols.index(col)] = GroupMgr.prefix + col
                    self.groupcols.append(col)
                elif groupmgr.matchcol(col) in self.cols:
                    self.cols.append(GroupMgr.prefix + col)
                    self.groupcols.append(col)

    # -- optional column sources ---------------------------------------------
    def addHeaderCols(self, names):
        for n in names:
            if n not in self.cols:
                self.cols.append(n)
                self.headercols.append(n)

    def setPositions(self, pos):
        self.positions = pos
        for c in pos.columns:
            if c not in self.cols:
                self.cols.append(c)

    def setNpSync(self, data):
        self.npdata = data
        self.npcols = []
        self.npsynccols = []
        for col in data.dtype.names:
            if col in self.cols or col in ('event', 'ifile'):
                self.npsynccols.append(col)
            else:
                self.npcols.append(col)
                self.cols.append(col)

    def skipCols(self, skip):
        for col in skip:
            if col in self.cols:
                del self.cols[self.cols.index(col)]

    def startout(self):
        print(*self.cols, sep='\t', file=self.out)

    # -- per-row enrichment ---------------------------------------------------
    def addNp(self, cur):
        if self.npdata is not None:
            r = self.npdata
            for sync in self.npsynccols:
                v = cur.get(sync)
                if v is None:
                    return
                try:
                    r = r[r[sync] == type(r[sync][0])(v)]
                except (ValueError, IndexError):
                    return
            if r.shape[0] == 1:
                for col in self.npcols:
                    cur[col] = r[col][0]

    def addConcatInt(self, cur):
        for row in self.concatint:
            vals = []
            for v in row:
                if cur.get(v) is None:
                    break
                vals.append(str(int(float(cur[v]))))
            else:
                cur[DatGenerator.concatjoiner.join(row)] = DatGenerator.concatjoiner.join(vals)

    def groupify(self, cur):
        if self.groupmgr is not None:
            for g in self.groupcols:
                cur[GroupMgr.prefix + g] = self.groupmgr.match(g, cur.get(self.groupmgr.matchcol(g), -1))

    def writerow(self, cur):
        cur.update(self.static)
        if self.h5.cols:
            self.h5.fill(cur)
        if self.positions is not None:
            self.positions.fill(cur)
        self.addNp(cur)
        self.addConcatInt(cur)
        self.groupify(cur)
        self.out.write('\t'.join(_fmt(cur.get(col)) for col in self.cols))
        self.out.write('\n')

    # -- stream parsing -------------------------------------------------------
    def parsestream(self, filename):
        """Parse one stream. Offsets are byte positions (file opened in binary mode)."""
        streamname = os.path.abspath(filename)
        want_vol = 'vol' in self.cols
        headers = set(self.headercols)
        cur = None
        crylst = []
        section = None            # None, 'peaks', 'refl'
        pos = 0
        with open(filename, 'rb') as f:
            for raw in f:
                start = pos
                pos += len(raw)
                line = raw.rstrip(b'\r\n')
                if section is not None:
                    if section == 'peaks' and line == PEAKS_END:
                        cur['pend'] = start
                        section = None
                    elif section == 'refl' and line == REFL_END:
                        cur['rend'] = start
                        section = None
                    continue
                if line == CHUNK_START:
                    cur = {'multi': 0, 'multiid': 0, 'sfile': streamname, 'istart': start}
                    crylst = [cur]
                    fields = CHUNK_FIELDS
                    continue
                if cur is None:
                    continue    # stream header, or text between concatenated streams
                if line == PEAKS_START:
                    cur['pstart'] = pos
                    section = 'peaks'
                elif line == REFL_START:
                    cur['rstart'] = pos
                    section = 'refl'
                elif line == CRYSTAL_START:
                    if cur['multi'] > 0:
                        cur = cur.copy()
                        for k in ('rstart', 'rend'):
                            cur.pop(k, None)
                        crylst.append(cur)
                    cur['multi'] += 1
                    cur['multiid'] += 1
                    cur['cstart'] = start
                    fields = CRYSTAL_FIELDS
                elif line == CRYSTAL_END:
                    cur['cend'] = pos
                    if want_vol and 'gamma' in cur:
                        cur['vol'] = _volume(cur)
                elif line == CHUNK_END:
                    for c in crylst:
                        c['iend'] = pos
                        c['multi'] = cur['multi']
                        self.writerow(c)
                    cur = None
                else:
                    text = line.decode('utf-8', errors='replace')
                    if headers and text.startswith('header/'):
                        name, _, val = text.partition(' = ')
                        name = name.split('/', 2)[2]
                        if name in headers:
                            cur[name] = val
                        continue
                    for prefix, handler in fields:
                        if text.startswith(prefix):
                            handler(text[len(prefix):], cur)
                            break

    def parselist(self, filename):
        with open(filename) as f:
            for line in f:
                parts = line.split()
                if len(parts) not in (1, 2):
                    continue
                cur = {}
                _ifile(parts[0], cur)
                if len(parts) == 2:
                    _event(parts[1], cur)
                cur['basename'] = os.path.basename(parts[0])
                self.writerow(cur)

    def close(self):
        self.h5.close()


# ---------------------------------------------------------------------------
# HDF5 per-frame metadata (--cxi/--h5, --cxiintegrate, --eiger)
# ---------------------------------------------------------------------------
class H5Fields:
    """Reads HDF5 datasets for each frame from the image (or EIGER master) file.

    Scalars apply to every frame; arrays whose first dimension covers the frame index are
    indexed by event. Whole datasets are read once per file, so per-frame lookups are cheap.
    For Cheetah .cxi files a sibling .h5 file is also searched (legacy LCLS behaviour).
    """

    def __init__(self, cols, integrate):
        self.cols = list(cols)
        self.integrate = set(integrate)
        self.name = None
        self.cache = {}
        self.missing = set()

    def _load(self, ifile):
        self.name = ifile
        self.cache = {}
        files = [ifile]
        if ifile.endswith('.cxi') and os.path.isfile(ifile[:-3] + 'h5'):
            files.append(ifile[:-3] + 'h5')
        for fn in files:
            try:
                fh = h5py.File(fn, 'r')
            except OSError:
                print("datgen: cannot open %s for HDF5 fields" % fn, file=sys.stderr)
                continue
            with fh:
                for col in self.cols:
                    if col in self.cache:
                        continue
                    obj = fh.get(col)
                    if isinstance(obj, h5py.Dataset):
                        self.cache[col] = obj[()]

    def fill(self, cur):
        ifile = cur.get('ifile')
        if ifile is None:
            return
        if ifile != self.name:
            self._load(ifile)
        event = cur.get('event')
        event = int(event) if event is not None else 0
        for col in self.cols:
            v = self.cache.get(col)
            if v is None:
                if col not in self.missing:
                    self.missing.add(col)
                    print("datgen: %s not found in %s; written as -1" % (col, ifile), file=sys.stderr)
                continue
            if np.ndim(v) > 0 and col not in self.integrate:
                v = v[event] if event < len(v) else None
            elif np.ndim(v) > 1:
                v = v[event] if event < len(v) else None
            if v is not None and col in self.integrate:
                v = _trapezoid(np.asarray(v, dtype=float))
            if v is not None and np.ndim(v) > 0:
                v = None   # multi-valued per frame and not integrated: cannot be one column
            if isinstance(v, bytes):
                v = v.decode(errors='replace')
            cur[col] = v

    def close(self):
        self.cache = {}


def scan_header_names(stream, maxchunks=50):
    """Names of 'header/...' values (from indexamajig --copy-header) in the first chunks."""
    names = []
    seen = 0
    with open(stream, 'rb') as f:
        for raw in f:
            if raw.startswith(b'header/'):
                name = raw.decode(errors='replace').split(' = ')[0].split('/', 2)[2]
                if name not in names:
                    names.append(name)
            elif raw.startswith(CHUNK_END):
                seen += 1
                if seen >= maxchunks:
                    break
    return names


def _volume(cur):
    ca = cos(radians(float(cur['alpha'])))
    cb = cos(radians(float(cur['beta'])))
    cg = cos(radians(float(cur['gamma'])))
    return float(cur['a']) * float(cur['b']) * float(cur['c']) * \
        sqrt(max(0.0, 1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg))


def _fmt(v):
    if v is None:
        return '-1'
    if isinstance(v, (float, np.floating)):
        return '%.8g' % v
    return str(v)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        description='Create a .dat table (one row per frame, or per crystal on multi-crystal frames) '
                    'from CrystFEL stream files or CrystFEL list files. Missing values are written as -1.',
        epilog='examples:\n'
               '  datgen.py -o run.dat run1.stream run2.stream\n'
               '  datgen.py --eiger --headers -o run.dat run.stream\n'
               '  datgen.py --positions plate.csv -o run.dat run.stream\n'
               '  datgen.py --grid 20 15 --serpentine -o run.dat run.stream',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', '-o', type=argparse.FileType('w'), default=sys.stdout, help='output file (default: stdout)')
    p.add_argument('--group', default=None, help='group file from groupgen.py (enumerates strings / defines custom groups)')
    p.add_argument('--builtincols', default=None, nargs='+', help='builtin columns to include, in this order (default: all)')
    p.add_argument('--cxi', '--h5', dest='cxi', action='append', default=[],
                   help='HDF5 dataset path read from the image file for each frame; scalars apply to all frames, '
                        'arrays are indexed by event. Repeat for several fields, e.g. --h5 /entry/sample/goniometer/omega')
    p.add_argument('--cxiintegrate', action='append', default=[],
                   help='HDF5 dataset holding 1D data per frame; stores its integral (trapezoid rule)')
    p.add_argument('--eiger', action='store_true',
                   help='add standard DECTRIS EIGER master-file metadata (detector distance, beam centre, '
                        'count time, wavelength, omega); paths absent from the file are written as -1')
    p.add_argument('--headers', action='store_true',
                   help="add a column for every 'header/...' value in the stream (indexamajig --copy-header)")
    p.add_argument('--header', action='append', default=[], help='add one stream header value by name (repeatable)')
    p.add_argument('--positions', default=None,
                   help='sample position table (csv/tsv) mapping ifile/event to plate well, drop or grid position; '
                        'see the manual, "Sample positions"')
    p.add_argument('--grid', nargs=2, type=int, default=None, metavar=('NX', 'NY'),
                   help='frames are a raster of NX columns by NY rows per image file: adds grid_col and grid_row from the event number')
    p.add_argument('--serpentine', action='store_true', help='with --grid: alternate rows run in reverse (snake scan)')
    p.add_argument('--static', action='append', default=[], nargs=2, metavar=('NAME', 'VALUE'),
                   help='add a column with the same value on every row, e.g. --static dataset jan2026')
    p.add_argument('--npfile', default=None,
                   help='numpy record array; columns matching output columns are used to look up rows, the rest are added')
    p.add_argument('--skipcols', default=[], nargs='+', help='remove these columns from the output')
    p.add_argument('--concatint', action='append', default=[], nargs='+',
                   help='join integer columns with "--" into one column, for grouping on several values')
    p.add_argument('files', nargs='+',
                   help='stream files, or list files (.lst/.txt); do not mix the two. List lines: "file [//event]"')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    listmode = os.path.splitext(args.files[0])[1] in ('.lst', '.txt')

    if args.builtincols is None:
        args.builtincols = DatGenerator.alllistcols if listmode else DatGenerator.allstreamcols

    h5cols = list(args.cxi)
    if args.eiger:
        h5cols += [f for f in EIGER_FIELDS if f not in h5cols]

    gmgr = GroupMgr(args.group, True) if args.group else None
    gen = DatGenerator(args.out, args.builtincols, h5cols, args.static, args.cxiintegrate, args.concatint, gmgr)

    headers = list(args.header)
    if args.headers and not listmode:
        for name in scan_header_names(args.files[0]):
            if name not in headers:
                headers.append(name)
    gen.addHeaderCols(headers)

    if args.positions or args.grid:
        gen.setPositions(posmod.PositionMap(args.positions, args.grid, args.serpentine))

    if args.npfile is not None:
        gen.setNpSync(np.load(args.npfile))
    gen.finishColumns()
    gen.groupmgr = gmgr
    gen.skipCols(args.skipcols)

    gen.startout()
    try:
        for f in args.files:
            if listmode:
                gen.parselist(f)
            else:
                gen.parsestream(f)
    finally:
        gen.close()
        if args.out is not sys.stdout:
            args.out.close()


if __name__ == '__main__':
    main()
