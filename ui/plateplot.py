# datview/ui/plateplot.py
# Plate / sample-position plot: any column, aggregated per well, drop and (optionally) raster
# point, laid out like the crystallisation plate. Selections drawn on it sync to all plots.

import numpy as np
from matplotlib.patches import Rectangle
from matplotlib.collections import LineCollection

try:
    from PyQt5.QtWidgets import QDialog, QFormLayout, QComboBox, QCheckBox, QDialogButtonBox, QVBoxLayout, QLabel
    from PyQt5.QtCore import Qt
except ImportError:
    from PyQt4.QtGui import QDialog, QFormLayout, QComboBox, QCheckBox, QDialogButtonBox, QVBoxLayout, QLabel
    from PyQt4.QtCore import Qt

from ui.plots import MyFigure, MyPlot, get_cmap

PLATES = {"Auto (from data)": None, "24-well (4 x 6)": (4, 6), "96-well (8 x 12)": (8, 12)}
AGGREGATES = ["mean", "median", "max", "min", "std", "count", "fraction > 0"]
XNAME = "plate_x"
YNAME = "plate_y"


def hasPlateColumns(model):
    cols = set(model.cols)
    return {"well_row", "well_col"} <= cols or {"grid_col", "grid_row"} <= cols


# ---------------------------------------------------------------------------
# Layout: table rows -> canvas cell coordinates
# ---------------------------------------------------------------------------
class PlateLayout:
    """Integer canvas coordinates for every row of the table.

    Wells are placed on the plate grid (row A at the top); each well is split horizontally
    into its drops; with a raster, each drop is drawn as a small grid_col x grid_row map.
    Rows without position information get NaN and are left out.
    """

    def __init__(self, model, plate=None, ndrops=None, raster=True):
        d = model.data
        n = len(d)
        cols = set(model.cols)
        haswell = {"well_row", "well_col"} <= cols
        hasgrid = {"grid_col", "grid_row"} <= cols and raster

        wr = d["well_row"].astype(float) if haswell else np.ones(n)
        wc = d["well_col"].astype(float) if haswell else np.ones(n)
        dr = d["drop"].astype(float) if "drop" in cols else np.ones(n)
        dr[dr < 1] = 1
        gc = d["grid_col"].astype(float) if hasgrid else np.zeros(n)
        gr = d["grid_row"].astype(float) if hasgrid else np.zeros(n)

        ok = (wr >= 1) & (wc >= 1) & (gc >= 0) & (gr >= 0)
        self.nrows, self.ncols = plate if plate else (int(np.max(wr[ok])) if ok.any() else 1,
                                                     int(np.max(wc[ok])) if ok.any() else 1)
        self.ndrops = ndrops or (int(np.max(dr[ok])) if ok.any() else 1)
        self.gx = int(np.max(gc[ok])) + 1 if hasgrid and ok.any() else 1
        self.gy = int(np.max(gr[ok])) + 1 if hasgrid and ok.any() else 1
        ok &= (wr <= self.nrows) & (wc <= self.ncols) & (dr <= self.ndrops)

        self.gapd = 1 if self.gx > 1 else 0          # between drops
        self.gapw = 2 if self.gx > 1 else 0          # between wells
        self.dropw = self.gx + self.gapd
        self.wellw = self.ndrops * self.dropw + self.gapw
        self.wellh = self.gy + self.gapw
        self.W = self.ncols * self.wellw
        self.H = self.nrows * self.wellh

        x = (wc - 1) * self.wellw + (dr - 1) * self.dropw + gc
        y = (wr - 1) * self.wellh + gr
        x[~ok] = np.nan
        y[~ok] = np.nan
        self.x = x          # integer-valued cell index (float so NaN marks missing)
        self.y = y
        self.haswell = haswell
        self.hasgrid = hasgrid

    def wellOf(self, cx, cy):
        r = int(cy // self.wellh)
        c = int(cx // self.wellw)
        within = cx - c * self.wellw
        drop = int(within // self.dropw)
        return r, c, drop, int(within - drop * self.dropw), int(cy - r * self.wellh)

    def regionMask(self):
        """1 inside drop areas, 0 in the gaps between drops and wells."""
        m = np.zeros((self.H, self.W))
        for r in range(self.nrows):
            for c in range(self.ncols):
                for d in range(self.ndrops):
                    x0 = c * self.wellw + d * self.dropw
                    y0 = r * self.wellh
                    m[y0:y0 + self.gy, x0:x0 + self.gx] = 1
        return m

    def outlines(self):
        """Line segments around each well (and drop when drops are wider than one cell)."""
        segs = []
        for r in range(self.nrows):
            for c in range(self.ncols):
                x0 = c * self.wellw
                y0 = r * self.wellh
                x1 = x0 + self.ndrops * self.dropw - self.gapd
                y1 = y0 + self.gy
                segs += [[(x0, y0), (x1, y0)], [(x1, y0), (x1, y1)], [(x1, y1), (x0, y1)], [(x0, y1), (x0, y0)]]
                if self.ndrops > 1 and self.gapd == 0:
                    for k in range(1, self.ndrops):
                        xd = x0 + k * self.dropw
                        segs.append([(xd, y0), (xd, y1)])
        return segs


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------
class MyPlatePlot(MyPlot):
    def __init__(self, model, field, agg, layout, fig, plt, parent):
        MyPlot.__init__(self, fig, plt, parent)
        self.model = model
        self.field = field
        self.agg = agg
        self.lay = layout
        self.img = None
        self.cnt = None
        self.clim = (None, None)
        self.fixedClim = None

        # Selection works on cell-centre coordinates, through filters on derived arrays
        self.cx = layout.x + 0.5
        self.cy = layout.y + 0.5
        self.manageX = True
        self.manageY = True
        self.registerFilters()

        self.listen(self.model.filterchange, self.requestDraw)
        self.listen(self.model.filterModelChange, self.onFilterModelChange)

        self.sel = Rectangle((0, 0), 0, 0, color='r', fill=False, linewidth=1.5)
        self.plt.set_xlim(0, layout.W)
        self.plt.set_ylim(layout.H, 0)
        self.onFilterChange()
        self.mydraw(False)

    def registerFilters(self):
        self.fieldfilterX = self.model.derivedSelectionFilter(XNAME, self.cx)
        self.fieldfilterX.modelchange.connect(self.onFilterChange)
        self.fieldfilterY = self.model.derivedSelectionFilter(YNAME, self.cy)
        self.fieldfilterY.modelchange.connect(self.onFilterChange)

    def onFilterModelChange(self):
        for f in (self.fieldfilterX, self.fieldfilterY):
            try:
                f.modelchange.disconnect(self.onFilterChange)
            except (TypeError, RuntimeError):
                pass
        self.model.selfilters.pop(XNAME, None)
        self.model.selfilters.pop(YNAME, None)
        self.registerFilters()
        self.onFilterChange()

    def aggregate(self, selected):
        """Per-cell statistic of self.field over all rows, or over the selected rows only."""
        L = self.lay
        keep = np.isfinite(L.x)
        if selected:
            keep &= self.model.rootfilter.getKeep()
        keep &= self.model.partitionfilter.getKeep()
        v = self.model.data[self.model.datafield(self.field)][keep].astype(float)
        idx = (L.y[keep] * L.W + L.x[keep]).astype(np.int64)
        size = L.W * L.H
        cnt = np.bincount(idx, minlength=size).astype(float)
        if self.agg == "count":
            out = cnt.copy()
        elif self.agg == "fraction > 0":
            out = np.bincount(idx, weights=(v > 0).astype(float), minlength=size) / np.maximum(cnt, 1)
        else:
            valid = v != -1            # -1 marks "no value" (e.g. unindexed) in dat files
            vi, vv = idx[valid], v[valid]
            n = np.bincount(vi, minlength=size).astype(float)
            if self.agg == "mean":
                out = np.bincount(vi, weights=vv, minlength=size) / np.maximum(n, 1)
            elif self.agg == "std":
                m = np.bincount(vi, weights=vv, minlength=size) / np.maximum(n, 1)
                m2 = np.bincount(vi, weights=vv * vv, minlength=size) / np.maximum(n, 1)
                out = np.sqrt(np.maximum(m2 - m * m, 0))
            else:
                out = np.full(size, np.nan)
                order = np.argsort(vi, kind="stable")
                vi, vv = vi[order], vv[order]
                cells, starts = np.unique(vi, return_index=True)
                func = {"median": np.median, "max": np.max, "min": np.min}[self.agg]
                for c, part in zip(cells, np.split(vv, starts[1:])):
                    out[c] = func(part)
            out[n == 0] = np.nan
        out[cnt == 0] = np.nan
        return out.reshape(L.H, L.W), cnt.reshape(L.H, L.W)

    def datadraw(self):
        L = self.lay
        both = self.drawBoth.isChecked() and self.model.isFiltered()
        layers = [(False, 0.3), (True, 1.0)] if both else [(self.drawSelection.isChecked(), 1.0)]
        # light grey where a drop has no data, white between wells and drops
        region = np.ma.masked_equal(L.regionMask(), 0)
        self.plt.imshow(region, cmap=get_cmap("Greys"), vmin=0, vmax=10, origin="upper",
                        extent=(0, L.W, L.H, 0), interpolation="nearest", aspect="equal")
        cmap = get_cmap(self.model.cfg.pixelcmap)
        cmap = cmap.with_extremes(bad=(0, 0, 0, 0)) if hasattr(cmap, "with_extremes") else cmap
        if self.fixedClim is None:
            # colour scale from all rows, so it does not jump when the selection changes
            full, _ = self.aggregate(False)
            self.fixedClim = (np.nanmin(full), np.nanmax(full)) if np.isfinite(full).any() else (0, 1)
        vmin = self.clim[0] if self.clim[0] is not None else self.fixedClim[0]
        vmax = self.clim[1] if self.clim[1] is not None else self.fixedClim[1]
        for selected, alpha in layers:
            self.img, self.cnt = self.aggregate(selected)
            im = self.plt.imshow(np.ma.masked_invalid(self.img), cmap=cmap, origin="upper", alpha=alpha,
                                 extent=(0, L.W, L.H, 0), interpolation="nearest", aspect="equal",
                                 vmin=vmin, vmax=vmax)
        self.plt.add_collection(LineCollection(L.outlines(), colors="0.35", linewidths=0.8))
        if self.cb is None:
            self.cb = self.fig.colorbar(im, ax=self.plt)
        else:
            self.cb.update_normal(im)
        self.plt.set_xticks([c * L.wellw + (L.ndrops * L.dropw - L.gapd) / 2 for c in range(L.ncols)])
        self.plt.set_xticklabels([str(c + 1) for c in range(L.ncols)])
        self.plt.set_yticks([r * L.wellh + L.gy / 2 for r in range(L.nrows)])
        self.plt.set_yticklabels([chr(ord("A") + r) if L.haswell else str(r + 1) for r in range(L.nrows)])
        self.plt.tick_params(length=0)
        self.plt.set_title("%s of %s" % (self.agg, self.model.prettyname(self.field)))

    def onReset(self, *args):
        self.plt.set_xlim(0, self.lay.W)
        self.plt.set_ylim(self.lay.H, 0)
        self.mydraw()

    def toolTip(self, event):
        if event.xdata is None or event.ydata is None or self.img is None or event.inaxes != self.plt:
            return ""
        L = self.lay
        cx, cy = int(event.xdata), int(event.ydata)
        if not (0 <= cx < L.W and 0 <= cy < L.H):
            return ""
        r, c, drop, gxi, gyi = L.wellOf(cx, cy)
        txt = "%s%d" % (chr(ord("A") + r), c + 1) if L.haswell else ""
        if L.ndrops > 1:
            txt += " drop %d" % (drop + 1)
        if L.hasgrid:
            txt += " grid (%d,%d)" % (gxi, gyi)
        val = self.img[cy, cx]
        txt += "\n%s: %s\nframes/crystals: %d" % (self.agg, "-" if np.isnan(val) else "%.4g" % val, self.cnt[cy, cx])
        return txt.strip()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------
class MyPlatePlotDialog(QDialog):
    def __init__(self, model, parent):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Plate / Sample Position Plot")
        self.model = model
        self.field = QComboBox()
        init = model.datafield(model.cfg.scatterInitialY) if model.cfg.scatterInitialY else None
        for i, col in enumerate(sorted(set(model.cols) - model.cfg.internalCols, key=model.prettyname)):
            self.field.addItem(model.prettyname(col), col)
            if col == init:
                self.field.setCurrentIndex(i)
        self.agg = QComboBox()
        self.agg.addItems(AGGREGATES)
        self.plate = QComboBox()
        self.plate.addItems(list(PLATES))
        self.drops = QComboBox()
        self.drops.addItems(["Auto", "1", "2", "3"])
        self.raster = QCheckBox("Show raster points within each drop")
        self.raster.setChecked(True)
        self.raster.setEnabled({"grid_col", "grid_row"} <= set(model.cols))

        form = QFormLayout()
        form.addRow("Colour by", self.field)
        form.addRow("Statistic", self.agg)
        form.addRow("Plate", self.plate)
        form.addRow("Drops per well", self.drops)
        form.addRow("", self.raster)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Values of -1 (not measured) are ignored except by 'count'.\n"
                             "Shift+drag on the plot to select wells/points; the selection syncs to all plots."))
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.accepted.connect(self.onAccept)

    def onAccept(self):
        field = self.field.itemData(self.field.currentIndex())
        drops = None if self.drops.currentText() == "Auto" else int(self.drops.currentText())
        layout = PlateLayout(self.model, PLATES[self.plate.currentText()], drops, self.raster.isChecked())
        p = MyFigure(parent=self.parent(), flags=Qt.Window)
        axis = p.fig.add_subplot(111)
        plot = MyPlatePlot(self.model, field, self.agg.currentText(), layout, p.fig, axis, p)
        p.plts.append(plot)
        p.setWindowTitle("%s - Plate Plot" % self.model.prettyname(field))
        p.resize(900, 650)
        p.show()
        return p
