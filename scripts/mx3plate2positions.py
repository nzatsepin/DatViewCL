#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# datview/scripts/mx3plate2positions.py
# Convert the plate CSV and plate log written by the MX3 SSX scan notebooks (SSXscanTools.py)
# into a DatView positions table for datgen.py --positions.

import argparse
import csv
import glob
import os
import re
import sys

WELL_RE = re.compile(r'^\s*([A-Pa-p])(\d{1,2})(?:[\s_]*well[\s_]*(\d))?\s*$')

# Columns written by SSXscanTools.Plate.open_plate_csvfile (2026 version)
CSV_COLS = {'counter': 'Scan Counter', 'window': 'Window Name', 'srow': 'scan row', 'scol': 'scan col',
            'x': 'scan x', 'y': 'scan y', 'plate': 'Plate Name', 'energy': 'Beam Energy',
            'det': 'detector z'}
# Optional columns added by the suggested SSXscanTools patch (docs/MX3_SCAN_SCRIPTS.md)
# Beam values passed through as DatView columns when present (patched csv, or the log)
BEAM_PASS = ['transmission', 'flux', 'beam_x_size', 'beam_y_size']
PATCH_COLS = {'nrows': 'number_of_rows', 'ncols': 'number_of_columns', 'gw': 'grid_width',
              'gh': 'grid_height', 'spot': 'spot', 'master': 'master_file'}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def read_plate_csv(path):
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("%s: no scans" % path)
    rows = [{k.strip(): (v or '').strip() for k, v in r.items() if k} for r in rows]
    missing = [c for c in ('counter', 'window', 'x', 'y') if CSV_COLS[c] not in rows[0]]
    if missing:
        raise SystemExit("%s: missing columns %s; is this a SSXscanTools plate csv?" %
                         (path, ", ".join(CSV_COLS[c] for c in missing)))
    return rows


def read_plate_log(path):
    """Per-scan prefect parameters from the plate log: {counter: {key: value}}.

    Scan blocks start with 'Scan Counter: N' followed by 'Scan horizontal position (mm)'.
    Region blocks (which also start with 'Scan Counter') are skipped."""
    scans = {}
    with open(path) as f:
        lines = [l.rstrip('\n') for l in f]
    i = 0
    while i < len(lines):
        m = re.match(r'Scan Counter:\s*(\d+)', lines[i])
        if m and i + 1 < len(lines) and lines[i + 1].startswith('Scan horizontal position'):
            counter = int(m.group(1))
            block = {}
            i += 1
            while i < len(lines) and lines[i].strip():
                key, sep, val = lines[i].partition(':')
                if sep:
                    block[key.strip()] = val.strip()
                i += 1
            scans[counter] = block
        i += 1
    return scans


def find_masters(paths):
    """{scan counter: master file name}. The counter is the second '_' field of the file
    name, as SSXscanTools' get_current_counter() reads it from grid_scans/*data*.h5."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += glob.glob(os.path.join(p, '*master*.h5'))
        else:
            files += glob.glob(p)
    out = {}
    for fn in files:
        parts = os.path.basename(fn).split('_')
        if len(parts) > 1 and parts[1].isdigit():
            out[int(parts[1])] = os.path.basename(fn)
    return out


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def convert(rows, log, masters, args):
    out = []
    warn = set()
    # group scans by window to tile them into one raster per window
    by_window = {}
    for r in rows:   # one map per window and spot (drop)
        by_window.setdefault((r[CSV_COLS['window']], r.get(PATCH_COLS['spot'], '')), []).append(r)

    for (window, _spot), scans in by_window.items():
        info = []
        for r in scans:
            counter = int(float(r[CSV_COLS['counter']]))
            p = log.get(counter, {})
            nrows = num(r.get(PATCH_COLS['nrows'])) or num(p.get('number_of_rows')) or args.grid_rows
            ncols = num(r.get(PATCH_COLS['ncols'])) or num(p.get('number_of_columns')) or args.grid_cols
            gw = num(r.get(PATCH_COLS['gw'])) or num(p.get('grid_width'))
            gh = num(r.get(PATCH_COLS['gh'])) or num(p.get('grid_height'))
            if nrows is None or ncols is None:
                raise SystemExit("scan %d: grid size unknown; give the plate log (--log) or --grid-size ROWS COLS" % counter)
            nrows, ncols = int(nrows), int(ncols)
            # raster step (um): explicit, else camera pixels per grid cell / camera scale
            sx, sy = args.step if args.step else (None, None)
            if sx is None and gw and gh:
                sx = gw / ncols / args.px_per_mm * 1000
                sy = gh / nrows / args.px_per_mm * 1000
                warn.add("step from grid_width/height and %g camera px/mm: %.2f x %.2f um" % (args.px_per_mm, sx, sy))
            beam = {'energy_keV': r.get(CSV_COLS['energy'], ''), 'det_dist_m': r.get(CSV_COLS['det'], '')}
            for k in BEAM_PASS:
                beam[k] = r.get(k) or p.get(k, '')
            info.append(dict(r=r, counter=counter, nrows=nrows, ncols=ncols, sx=sx, sy=sy, beam=beam,
                             x=num(r[CSV_COLS['x']]), y=num(r[CSV_COLS['y']])))

        # Tiling: scans of one window are placed side by side in the order of their motor
        # positions (distinct horizontal positions -> scan columns, vertical -> scan rows), each
        # scan taking ncols x nrows cells. This assumes neighbouring scans abut, as in the MX3
        # notebooks (scan spacing = grid size x step); with gaps, the map is compressed but the
        # physical positions (pos_x_um, pos_y_um) stay correct.
        tile = args.tile and len(info) > 1
        if tile:
            xs = sorted({round(i['x'], 4) for i in info}, reverse=not args.flip_x)
            ys = sorted({round(i['y'], 4) for i in info}, reverse=args.flip_y)
            xref, yref = xs[0], ys[0]

        m = WELL_RE.match(window)
        for i in info:
            r = i['r']
            master = r.get(PATCH_COLS['master']) or masters.get(i['counter']) or args.template.format(counter=i['counter'])
            if i['counter'] not in masters and not r.get(PATCH_COLS['master']):
                warn.add("no master file found for some scans: using pattern %s" % args.template)
            row = {'ifile': os.path.basename(master), 'grid_nx': i['ncols'], 'grid_ny': i['nrows'],
                   'fast_axis': args.fast_axis, 'serpentine': 'yes' if args.serpentine else '',
                   'scan_counter': i['counter'], 'window': window.replace(' ', '_'),
                   'scan_row': r.get(CSV_COLS['srow'], ''), 'scan_col': r.get(CSV_COLS['scol'], ''),
                   'scan_x_mm': r[CSV_COLS['x']], 'scan_y_mm': r[CSV_COLS['y']],
                   'plate': r.get(CSV_COLS['plate'], '').replace(' ', '_')}
            row.update({k: v for k, v in i['beam'].items() if v not in ('', None)})
            if m:
                row['well'] = '%s%s' % (m.group(1).upper(), int(m.group(2)))
                spot = r.get(PATCH_COLS['spot']) or m.group(3) or args.drop
                if spot:
                    row['drop'] = int(spot)
            elif args.drop:
                row['drop'] = args.drop
            if i['sx']:
                row['step_x_um'] = '%.3f' % i['sx']
                row['step_y_um'] = '%.3f' % i['sy']
            if tile:
                cx = xs.index(round(i['x'], 4))
                cy = ys.index(round(i['y'], 4))
                row['grid_col_offset'] = cx * i['ncols']
                row['grid_row_offset'] = cy * i['nrows']
                if i['sx']:
                    # physical position relative to the window's first scan, in um
                    px = abs(i['x'] - xref) * 1000
                    py = abs(i['y'] - yref) * 1000
                    row['x0_um'] = '%.2f' % (px - row['grid_col_offset'] * i['sx'])
                    row['y0_um'] = '%.2f' % (py - row['grid_row_offset'] * i['sy'])
            out.append(row)
    return out, warn


FIELDS = ['ifile', 'well', 'drop', 'grid_nx', 'grid_ny', 'fast_axis', 'serpentine', 'grid_col_offset',
          'grid_row_offset', 'step_x_um', 'step_y_um', 'x0_um', 'y0_um', 'scan_counter', 'window', 'scan_row', 'scan_col',
          'scan_x_mm', 'scan_y_mm', 'plate', 'energy_keV', 'det_dist_m'] + BEAM_PASS


def main():
    p = argparse.ArgumentParser(
        description='Make a DatView positions table from the MX3 SSX scan notebooks\' plate CSV '
                    '(<sample>_plate_csv.txt) and plate log (<sample>_plate_log.txt).',
        epilog='example:\n'
               '  mx3plate2positions.py --csv sbig_plate_csv.txt --log sbig_plate_log.txt \\\n'
               '      --masters /data/.../sbig/grid_scans -o sbig_positions.csv\n'
               '  datgen.py --positions sbig_positions.csv -o sbig.dat sbig*.stream\n\n'
               'Check the frame order of one scan before trusting the maps (see docs/MX3_SCAN_SCRIPTS.md).',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--csv', required=True, help='plate csv written by SSXscanTools.Plate.append_scan_info_to_plate_csvfile')
    p.add_argument('--log', default=None, help='plate log (gives the grid rows/columns and size of each scan)')
    p.add_argument('--masters', nargs='+', default=[], help='grid_scans directory or master files, to get the real file names')
    p.add_argument('--template', default='*_{counter}_master.h5',
                   help='file name pattern used when no master file is found (default: *_{counter}_master.h5)')
    p.add_argument('--grid-size', nargs=2, type=int, default=None, metavar=('ROWS', 'COLS'),
                   help='grid rows and columns of every scan, if not in the log/csv')
    p.add_argument('--fast-axis', choices=['y', 'x'], default='y',
                   help='direction of continuous motion within a scan: y = one vertical line per column '
                        '(MD3 raster on alignment_y; default), x = one horizontal line per row')
    p.add_argument('--serpentine', action='store_true', help='alternate lines are collected in reverse')
    p.add_argument('--step', nargs=2, type=float, default=None, metavar=('DX_UM', 'DY_UM'), help='raster step in micrometres')
    p.add_argument('--px-per-mm', type=float, default=513.0,
                   help='camera scale used to convert grid_width/height (pixels) to mm when --step is not given '
                        '(default 513, from the plate-alignment notebook; check for your zoom)')
    p.add_argument('--no-tile', dest='tile', action='store_false',
                   help='do not join the scans of one window into one raster map')
    p.add_argument('--flip-x', action='store_true', help='scan columns run towards increasing horizontal motor position')
    p.add_argument('--flip-y', action='store_true', help='scan rows run towards decreasing vertical motor position')
    p.add_argument('--drop', type=int, default=None, help='drop/spot number for all scans, if not in the window name or csv')
    p.add_argument('-o', '--out', required=True, help='positions table to write')
    a = p.parse_args()
    a.grid_rows, a.grid_cols = a.grid_size if a.grid_size else (None, None)

    rows = read_plate_csv(a.csv)
    log = read_plate_log(a.log) if a.log else {}
    masters = find_masters(a.masters)
    out, warn = convert(rows, log, masters, a)
    with open(a.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(out)
    print("wrote %s: %d scans, %d windows" % (a.out, len(out), len({r['window'] for r in out})))
    for m in sorted(warn):
        print("note:", m, file=sys.stderr)


if __name__ == '__main__':
    main()
