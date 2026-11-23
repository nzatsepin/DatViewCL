#!/usr/bin/env python3
"""
Synthetic dat table on the layout of SSX_GridScans.ipynb (2 x 12 scans of 50 x 10 points),
positioned through the real import path (mx3plate2positions.py output + api.positions).

Invented effects: crystals only inside an elliptical drop; unit cell a larger near the drop
edge (drying); fewer peaks towards the edge. Not measured data.
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api.positions import PositionMap  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("positions", help="output of mx3plate2positions.py for tests/data/mx3")
    ap.add_argument("out")
    a = ap.parse_args()
    rng = np.random.default_rng(5)
    pm = PositionMap(a.positions)
    scans = list(csv.DictReader(open(a.positions)))
    rows = []
    for s in scans:
        n = int(s["grid_nx"]) * int(s["grid_ny"])
        name = "/data/mx3/sbig/grid_scans/sbig-filtered-0-22_%s_master.h5" % s["scan_counter"]
        for ev in range(n):
            cur = {"ifile": name, "event": ev}
            pm.fill(cur)
            rows.append(cur)
    gx = np.array([r["grid_col"] for r in rows], float)
    gy = np.array([r["grid_row"] for r in rows], float)
    r2 = ((gx - 62) / 52) ** 2 + ((gy - 48) / 40) ** 2
    inside = r2 < 1
    indexed = inside & (rng.random(len(rows)) < 0.45 * (1 - 0.5 * r2))
    a_ax = 79.1 + 0.5 * np.clip(r2, 0, 1) ** 2 + rng.normal(0, 0.05, len(rows))
    npeak = rng.poisson(np.where(indexed, 250 * (1.1 - r2.clip(0, 1)), 8))
    cols = ["ifile", "event", "npeak", "a", "multi", "multiid", "reslim", "grid_col", "grid_row",
            "pos_x_um", "pos_y_um", "scan_counter", "window"]
    with open(a.out, "w") as f:
        f.write("\t".join(cols) + "\n")
        for i, r in enumerate(rows):
            vals = [r["ifile"], r["event"], npeak[i], "%.4f" % (a_ax[i] / 10) if indexed[i] else -1,
                    int(indexed[i]), int(indexed[i]), "%.3f" % rng.normal(5, 0.4) if indexed[i] else -1,
                    r["grid_col"], r["grid_row"], "%.1f" % r.get("pos_x_um", -1), "%.1f" % r.get("pos_y_um", -1),
                    r["scan_counter"], r["window"]]
            f.write("\t".join(str(v) for v in vals) + "\n")
    print("wrote", a.out, len(rows), "frames,", int(indexed.sum()), "indexed")


if __name__ == "__main__":
    main()
