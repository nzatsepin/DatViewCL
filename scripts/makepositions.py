#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# datview/scripts/makepositions.py
# Build or extend a positions table (for datgen.py --positions) one grid scan at a time.
# Run once per scan, e.g. from the end of a beamline grid-scan script.

import argparse
import csv
import os

FIELDS = ['ifile', 'event_start', 'event_end', 'well', 'drop', 'grid_nx', 'grid_ny', 'serpentine',
          'step_x_um', 'step_y_um', 'x0_um', 'y0_um', 'plate']


def main():
    p = argparse.ArgumentParser(
        description='Append one grid scan to a DatView positions table (csv). Creates the file if needed.',
        epilog='example (well B3, drop 2, 20 x 15 snake raster, 10 um steps, frames 0-299 of one master file):\n'
               '  makepositions.py -o plate1_positions.csv --ifile scan_0012_master.h5 --well B3 --drop 2 \\\n'
               '      --grid 20 15 --serpentine --step 10 10 --plate plate1',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-o', '--out', required=True, help='positions table to create or append to')
    p.add_argument('--ifile', required=True, help='image file of the scan (EIGER: the *_master.h5); matched by name, so a path is optional')
    p.add_argument('--first', type=int, default=0, help='event number of the first frame of this scan in the file (default 0)')
    p.add_argument('--nframes', type=int, default=None, help='frames in the scan (default: grid NX*NY, or open-ended)')
    p.add_argument('--well', default=None, help='plate well label, e.g. A1, H12')
    p.add_argument('--drop', type=int, default=None, help='drop number within the well (1, 2, ...)')
    p.add_argument('--grid', nargs=2, type=int, default=None, metavar=('NX', 'NY'), help='raster points per line (fast axis) and number of lines')
    p.add_argument('--serpentine', action='store_true', help='alternate lines are collected in reverse (snake scan)')
    p.add_argument('--step', nargs=2, type=float, default=None, metavar=('DX', 'DY'), help='raster step in micrometres (adds pos_x_um, pos_y_um)')
    p.add_argument('--origin', nargs=2, type=float, default=None, metavar=('X0', 'Y0'), help='position of the first raster point in micrometres (default 0 0)')
    p.add_argument('--plate', default=None, help='plate name, if several plates go into one table')
    a = p.parse_args()

    n = a.nframes if a.nframes is not None else (a.grid[0] * a.grid[1] if a.grid else None)
    row = {'ifile': a.ifile, 'event_start': a.first, 'event_end': '' if n is None else a.first + n - 1,
           'well': a.well or '', 'drop': a.drop or '',
           'grid_nx': a.grid[0] if a.grid else '', 'grid_ny': a.grid[1] if a.grid else '',
           'serpentine': 'yes' if a.serpentine else '',
           'step_x_um': a.step[0] if a.step else '', 'step_y_um': a.step[1] if a.step else '',
           'x0_um': a.origin[0] if a.origin else '', 'y0_um': a.origin[1] if a.origin else '',
           'plate': a.plate or ''}
    new = not os.path.exists(a.out) or os.path.getsize(a.out) == 0
    with open(a.out, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


if __name__ == '__main__':
    main()
