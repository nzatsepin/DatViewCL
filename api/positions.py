# datview/api/positions.py
# Sample position mapping for datgen.py: which plate well / drop / raster point each frame came from.

import csv
import fnmatch
import os
import re

WELL_RE = re.compile(r'^\s*([A-Za-z])\s*0*(\d+)\s*$')

# Columns understood by DatView's plate plot. Everything else in a positions table is copied
# through unchanged (numbers stay numbers, text becomes a categorical column).
POSITION_COLS = ['well', 'well_row', 'well_col', 'drop', 'grid_col', 'grid_row', 'pos_x_um', 'pos_y_um']

# Columns that control how a row is matched or how grid positions are computed; not output.
KEY_COLS = {'ifile', 'event', 'event_start', 'event_end', 'grid_nx', 'grid_ny', 'serpentine',
            'fast_axis', 'grid_col_offset', 'grid_row_offset',
            'step_x_um', 'step_y_um', 'x0_um', 'y0_um'}


def parse_well(label):
    """'B07' -> (2, 7). Rows are letters A=1, B=2, ...; returns (None, None) if not a well label."""
    m = WELL_RE.match(str(label))
    if not m:
        return None, None
    return ord(m.group(1).upper()) - ord('A') + 1, int(m.group(2))


def _truthy(v):
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'snake', 'serpentine')


class PositionMap:
    """Assigns position columns to each datgen row.

    Sources, in order of precedence:
      1. a table (csv/tsv) keyed by image file and optionally event or event range;
      2. raster geometry (--grid NX NY, or grid_nx/grid_ny columns in the table) that turns
         the event number into grid_col/grid_row (0-based, row 0 = first line scanned).

    Image files are matched by full path first, then by file name alone, so a table written
    on the beamline still matches after the data are moved. An ifile containing * ? or [ ]
    is a shell-style pattern matched against the file name (e.g. "*_12_master.h5").
    """

    def __init__(self, table=None, grid=None, serpentine=False):
        self.rows_exact = {}     # (key, event) -> row
        self.rows_range = {}     # key -> [(start, end, row)]
        self.rows_file = {}      # key -> row
        self.patterns = []       # (pattern, row) for ifile values with wildcards
        self.resolved = {}       # basename -> concrete key, cache of pattern matches
        self.extra = []
        self.default_grid = (grid[0], grid[1], serpentine) if grid else None
        self._has_steps = False
        if table:
            self._load(table)
            self.columns = ['well', 'well_row', 'well_col', 'drop', 'grid_col', 'grid_row']
            if self._has_steps:
                self.columns += ['pos_x_um', 'pos_y_um']
            self.columns += self.extra
        else:
            self.columns = ['grid_col', 'grid_row']

    def _load(self, path):
        with open(path, newline='') as f:
            lines = [l for l in f if l.strip() and not l.lstrip().startswith('#')]
        dialect = csv.excel_tab if lines and '\t' in lines[0] else csv.excel
        reader = csv.DictReader(lines, dialect=dialect)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        if 'ifile' not in reader.fieldnames:
            raise SystemExit("positions table %s needs an 'ifile' column" % path)
        for h in reader.fieldnames:
            if h not in KEY_COLS and h not in POSITION_COLS:
                self.extra.append(h)
        self._has_steps = 'step_x_um' in reader.fieldnames or 'step_y_um' in reader.fieldnames
        for row in reader:
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            key = row['ifile']
            if any(ch in key for ch in '*?['):
                self.patterns.append(key)
            if row.get('event', '') not in ('', None):
                self.rows_exact[(key, int(row['event']))] = row
            elif row.get('event_start', '') not in ('', None):
                start = int(row['event_start'])
                end = int(row['event_end']) if row.get('event_end') not in ('', None) else 1 << 62
                self.rows_range.setdefault(key, []).append((start, end, row))
            else:
                self.rows_file[key] = row

    def _keys(self, ifile):
        base = os.path.basename(ifile)
        keys = [ifile, base]
        if self.patterns:
            if base not in self.resolved:
                self.resolved[base] = next((p for p in self.patterns if fnmatch.fnmatchcase(base, p)), None)
            if self.resolved[base] is not None:
                keys.append(self.resolved[base])
        return keys

    def _lookup(self, ifile, event):
        for key in self._keys(ifile):
            if event is not None and (key, event) in self.rows_exact:
                return self.rows_exact[(key, event)]
            for start, end, row in self.rows_range.get(key, ()):
                if event is not None and start <= event <= end:
                    return row
            if key in self.rows_file:
                return self.rows_file[key]
        return None

    def fill(self, cur):
        ifile = cur.get('ifile')
        if ifile is None:
            return
        ev = cur.get('event')
        event = int(ev) if ev is not None else None
        row = self._lookup(ifile, event) or {}

        for c in self.extra:
            v = row.get(c)
            if v not in (None, ''):
                cur[c] = v.replace('\t', ' ').replace(' ', '_') if isinstance(v, str) else v

        well = row.get('well')
        r, c = parse_well(well) if well else (None, None)
        r = row.get('well_row') or r
        c = row.get('well_col') or c
        if r is not None:
            if isinstance(r, str) and r.isalpha():
                r = ord(r.upper()) - ord('A') + 1
            cur['well_row'] = r
            cur['well_col'] = c
            cur['well'] = well if well else '%s%s' % (chr(ord('A') + int(r) - 1), c)
        if row.get('drop') not in (None, ''):
            cur['drop'] = row['drop']

        # Raster position from event number
        nx = row.get('grid_nx') or (self.default_grid[0] if self.default_grid else None)
        ny = row.get('grid_ny') or (self.default_grid[1] if self.default_grid else None)
        snake = _truthy(row['serpentine']) if row.get('serpentine') not in (None, '') else \
            (self.default_grid[2] if self.default_grid else False)
        if 'grid_col' in row and row['grid_col'] != '':
            gx, gy = int(row['grid_col']), int(row['grid_row'])
        elif nx and event is not None:
            nx, ny = int(nx), int(ny) if ny else None
            first = 0
            for key in self._keys(ifile):
                for start, end, rr in self.rows_range.get(key, []):
                    if rr is row:
                        first = start
            k = event - first
            if str(row.get('fast_axis', 'x')).strip().lower() == 'y':
                # lines run along y (e.g. MD3 rasters: one vertical line per column)
                if not ny:
                    gx = gy = None
                else:
                    gx, gy = divmod(k, ny)
                    if snake and gx % 2 == 1:
                        gy = ny - 1 - gy
                    if gx >= nx:
                        gx = gy = None
            else:
                gy, gx = divmod(k, nx)
                if snake and gy % 2 == 1:
                    gx = nx - 1 - gx
                if ny is not None and gy >= ny:
                    gx = gy = None
            if gx is not None:
                gx += int(float(row.get('grid_col_offset') or 0))
                gy += int(float(row.get('grid_row_offset') or 0))
        else:
            gx = gy = None
        if gx is not None:
            cur['grid_col'] = gx
            cur['grid_row'] = gy
            sx, sy = row.get('step_x_um'), row.get('step_y_um')
            if sx not in (None, ''):
                cur['pos_x_um'] = float(row.get('x0_um') or 0) + gx * float(sx)
            if sy not in (None, ''):
                cur['pos_y_um'] = float(row.get('y0_um') or 0) + gy * float(sy)
