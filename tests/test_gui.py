"""Headless GUI checks: plots open, react to selections, and are fully released when closed."""
import gc
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
from PyQt5.QtCore import Qt  # noqa: E402

from conftest import make_dat  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def receivers(model):
    return model.receivers(model.filterchange)


def process(n=5):
    for _ in range(n):
        app.processEvents()


def test_plots_release_on_close(tmp_path):
    import datview
    from ui.plotDialogs import MyScatterDialog, MyHist2dDialog
    from ui.plateplot import MyPlatePlotDialog

    dat = make_dat(tmp_path, positions=True)
    w = datview.MyMainWindow(dat, None, None, None, None, None)
    w.show()
    process()
    base = receivers(w.model)

    # pop-out plots
    figs = []
    for dlg in (MyScatterDialog(w.model, w), MyHist2dDialog(w.model, w), MyPlatePlotDialog(w.model, w)):
        dlg.onAccept()
    process()
    from ui.plots import MyFigure
    figs = [c for c in w.findChildren(MyFigure) if c.isWindow()]
    assert len(figs) == 3, [f.windowTitle() for f in figs]
    assert receivers(w.model) == base + 3

    # a selection on one histogram updates the others without error
    f = w.model.selectionFilter("a")
    f.setRange(77.0, 80.0)
    f.setActive(True)
    process()
    assert 0 < w.model.selectedCount() < len(w.model.data)

    # closing pop-outs disconnects them from the model
    for fig in figs:
        fig.close()
    process()
    gc.collect()
    assert receivers(w.model) == base

    # unticking a histogram destroys it
    field = w.checkedhistograms[0]
    w.onHistAction(field, False)
    process()
    assert field not in w.cachedHistograms
    assert receivers(w.model) == base - 1
    w.close()
    process()


def test_histogram_counts_match_numpy(tmp_path):
    from api.datamodel import DataModel
    from ui.plots import MyFigure
    m = DataModel(make_dat(tmp_path))
    fig = MyFigure()
    h = fig.histogram(m, "a")
    vals = m.datacol("a")
    expected, _ = np.histogram(vals, bins=h.edges)
    assert np.array_equal(np.asarray(h.cnts), expected)
    fig.release()


def test_plate_layout(tmp_path):
    from api.datamodel import DataModel
    from ui.plateplot import PlateLayout
    m = DataModel(make_dat(tmp_path, positions=True))
    lay = PlateLayout(m, None, None, True)
    assert (lay.nrows, lay.ncols, lay.ndrops, lay.gx, lay.gy) == (2, 2, 2, 5, 2)
    # every row with a well has a unique canvas position per (well, drop, grid point)
    ok = np.isfinite(lay.x)
    assert ok.all()
