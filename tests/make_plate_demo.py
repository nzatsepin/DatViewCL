#!/usr/bin/env python3
"""
Synthetic fixed-target plate dataset for demonstrating the plate plot.

Writes a .dat table (no stream behind it) for a 96-well plate, 2 drops per well, each drop
raster-scanned on a 10 x 8 grid. Built-in effects, so the plate plot has something to show:
  * evaporation: the unit cell a axis grows with collection order (wells collected later have
    been open longer), and wells on the plate edge are drier still;
  * crystals sit mostly in the drop centre: indexing rate falls towards the drop edge;
  * drop 2 of each well has fewer crystals than drop 1;
  * well D7 is empty.
All values are invented; this is not measured data.
"""
import argparse
import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    nrow, ncol, ndrop, gx, gy = 8, 12, 2, 10, 8
    rows = []
    order = 0
    for r in range(1, nrow + 1):
        cols = range(1, ncol + 1) if r % 2 else range(ncol, 0, -1)   # snake through the plate
        for c in cols:
            for d in (1, 2):
                for k in range(gx * gy):
                    gyi, gxi = divmod(k, gx)
                    if gyi % 2:
                        gxi = gx - 1 - gxi
                    rows.append((r, c, d, gxi, gyi, order))
                order += 1
    t = pd.DataFrame(rows, columns=["well_row", "well_col", "drop", "grid_col", "grid_row", "order"])
    n = len(t)

    edge = (t.well_row.isin([1, nrow]) | t.well_col.isin([1, ncol])).to_numpy()
    rx = (t.grid_col - (gx - 1) / 2) / (gx / 2)
    ry = (t.grid_row - (gy - 1) / 2) / (gy / 2)
    rad2 = (rx ** 2 + ry ** 2).to_numpy()
    p_idx = 0.55 * np.clip(1 - 0.75 * rad2, 0.02, 1) * np.where(t["drop"] == 2, 0.6, 1.0)
    p_idx[(t.well_row == 4) & (t.well_col == 7)] = 0
    indexed = rng.random(n) < p_idx
    multi = np.where(indexed, 1 + (rng.random(n) < 0.12 * p_idx), 0)

    frac = t.order.to_numpy() / t.order.max()
    a = 79.10 * (1 + 0.004 * frac + 0.003 * edge) + rng.normal(0, 0.06, n)
    c = 37.95 * (1 + 0.002 * frac + 0.002 * edge) + rng.normal(0, 0.04, n)
    reslim = np.clip(rng.normal(5.2 - 0.8 * edge - 0.6 * frac, 0.45, n), 1.0, 7.5)   # nm^-1
    npeak = rng.poisson(np.where(indexed, 180 + 120 * (1 - rad2.clip(0, 1)), 12))

    t["ifile"] = ["/data/mx3/demo/plate1/%s%d_d%d_master.h5" % (chr(64 + r), c, d)
                  for r, c, d in zip(t.well_row, t.well_col, t["drop"])]
    t["event"] = t.grid_row * gx + np.where(t.grid_row % 2, gx - 1 - t.grid_col, t.grid_col)
    t["well"] = [chr(64 + r) + str(c) for r, c in zip(t.well_row, t.well_col)]
    t["indby"] = np.where(indexed, "xgandalf-nolatt-cell", "none")
    t["npeak"] = npeak
    t["multi"] = multi
    t["multiid"] = np.where(indexed, 1, 0)
    t["a"] = np.where(indexed, a / 10, -1)          # nm, as in streams
    t["b"] = t["a"]
    t["c"] = np.where(indexed, c / 10, -1)
    for ang in ("alpha", "beta", "gamma"):
        t[ang] = np.where(indexed, 90.0, -1)
    t["reslim"] = np.where(indexed, reslim, -1)
    t["vol"] = np.where(indexed, (a / 10) ** 2 * (c / 10), -1)
    t["nref"] = np.where(indexed, rng.poisson(900, n), -1)
    cols = ["ifile", "event", "indby", "npeak", "a", "b", "c", "alpha", "beta", "gamma", "reslim", "nref",
            "multiid", "multi", "vol", "well", "well_row", "well_col", "drop", "grid_col", "grid_row"]
    t[cols].to_csv(args.out, sep="\t", index=False, float_format="%.5f")
    print("wrote %s: %d frames, %d indexed" % (args.out, n, int(indexed.sum())))


if __name__ == "__main__":
    main()
