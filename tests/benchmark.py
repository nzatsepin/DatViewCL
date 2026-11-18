#!/usr/bin/env python3
"""
Memory and interaction-speed benchmark for DatView (headless).

  python tests/benchmark.py make  big.dat --rows 500000 --template tests/data/....dat
  python tests/benchmark.py run   big.dat [--code /path/to/DatView]   -> JSON on stdout

`run` loads the table in the main window with the default 6 histograms plus 6 more, then
times a selection change, opens and closes 5 scatter windows, and times the same selection
change again. --code allows the same measurement on another DatView checkout (e.g. 2019).
"""
import argparse
import gc
import json
import os
import sys
import time


def rss_mb():
    with open('/proc/self/statm') as f:
        return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 1e6


def make(args):
    import numpy as np
    import pandas as pd
    t = pd.read_csv(args.template, sep=r'\s+', index_col=False)
    reps = int(np.ceil(args.rows / len(t)))
    big = pd.concat([t] * reps, ignore_index=True).iloc[:args.rows].copy()
    n = len(big)
    # Realistic file paths: one master file per 1000 frames, long beamline-style paths
    run = np.arange(n) // 1000
    big['ifile'] = ['/data/mx3/2026/p12345/raw/plateA/well_%04d/grid_scan_%04d_master.h5' % (r % 96, r) for r in run]
    big['event'] = np.arange(n) % 1000
    rng = np.random.default_rng(0)
    for c in ('a', 'b', 'c'):
        ok = big[c] != -1
        big.loc[ok, c] = big.loc[ok, c] + rng.normal(0, 0.05, ok.sum())
    big.to_csv(args.out, sep='\t', index=False)
    print('wrote', args.out, n, 'rows', os.path.getsize(args.out) / 1e6, 'MB')


def run(args):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    code = os.path.abspath(args.code)
    sys.path.insert(0, code)
    os.chdir(code)
    import numpy as np
    import matplotlib
    import matplotlib.pyplot as plt
    # Shims so the 2019 code can run on numpy 2 / matplotlib >= 3.9 for comparison only
    import numpy.lib.npyio as npyio
    if not hasattr(npyio, 'asstr'):
        npyio.asstr = str
    if not hasattr(plt.cm, 'get_cmap'):
        plt.cm.get_cmap = lambda name: matplotlib.colormaps[name]
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import datview
    from ui.plotDialogs import MyScatterDialog

    def settle():
        for _ in range(5):
            app.processEvents()

    out = {'code': code}
    base = rss_mb()
    t0 = time.time()
    w = datview.MyMainWindow(args.dat, None, None, None, None, None)
    w.show()
    settle()
    out['rows'] = len(w.model.data)
    out['load_s'] = round(time.time() - t0, 2)
    for f in ['reslim', 'nref', 'npeak', 'prorad', 'vol', 'multi']:
        w.onHistAction(f, True)
    settle()
    out['rss_loaded_12plots_MB'] = round(rss_mb() - base, 1)

    v = w.model.data['reslim']
    v = np.sort(v[v != -1])
    q = lambda p: float(v[int(p * (len(v) - 1))])

    def select(lo, hi):
        f = w.model.selectionFilter('reslim')
        t = time.time()
        f.setRange(lo, hi)
        f.setActive(True)
        settle()
        return round(time.time() - t, 2), int(np.count_nonzero(w.model.rootfilter.getKeep()))

    out['select_s_before'] = select(q(0.2), q(0.8))
    for _ in range(5):
        d = MyScatterDialog(w.model, w)
        d.onAccept()
        settle()
    out['rss_5_scatter_open_MB'] = round(rss_mb() - base, 1)
    out['select_s_scatter_open'] = select(q(0.1), q(0.6))
    from ui.plots import MyFigure
    for fig in [c for c in w.findChildren(MyFigure) if c.isWindow()]:
        fig.close()
    settle()
    gc.collect()
    settle()
    out['rss_after_close_MB'] = round(rss_mb() - base, 1)
    out['select_s_after_close'] = select(q(0.3), q(0.9))
    out['model_listeners_after_close'] = w.model.receivers(w.model.filterchange)
    import resource
    out['peak_rss_MB'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3, 1)
    print(json.dumps(out))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('make')
    m.add_argument('out')
    m.add_argument('--rows', type=int, default=500000)
    m.add_argument('--template', required=True)
    r = sub.add_parser('run')
    r.add_argument('dat')
    r.add_argument('--code', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = p.parse_args()
    make(a) if a.cmd == 'make' else run(a)
