"""MX3 scan-notebook import: plate csv + log -> positions -> grid coordinates per frame."""
import csv
import os
import subprocess
import sys

import pytest

from conftest import ROOT

MX3 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mx3")
SCRIPT = os.path.join(ROOT, "scripts", "mx3plate2positions.py")


def run(tmp_path, *extra):
    out = str(tmp_path / "pos.csv")
    subprocess.run([sys.executable, SCRIPT, "--csv", os.path.join(MX3, "gridscan_plate_csv.txt"),
                    "--log", os.path.join(MX3, "gridscan_plate_log.txt"), "-o", out] + list(extra),
                   check=True, capture_output=True)
    return out


def frame(pm, counter, event):
    cur = {"ifile": "/data/sbig-filtered-0-22_%d_master.h5" % counter, "event": event}
    pm.fill(cur)
    return cur


def test_log_and_csv_parsed(tmp_path):
    rows = list(csv.DictReader(open(run(tmp_path))))
    assert len(rows) == 24
    assert {r["grid_nx"] for r in rows} == {"10"} and {r["grid_ny"] for r in rows} == {"50"}
    # scans tile to a 120 x 100 raster: 12 scans across, 2 down
    offs = {(int(r["grid_col_offset"]), int(r["grid_row_offset"])) for r in rows}
    assert offs == {(10 * i, 50 * j) for i in range(12) for j in range(2)}


def test_frame_to_grid_fast_y(tmp_path):
    from api.positions import PositionMap
    pm = PositionMap(run(tmp_path))
    assert (frame(pm, 0, 0)["grid_col"], frame(pm, 0, 0)["grid_row"]) == (0, 0)
    assert (frame(pm, 0, 49)["grid_col"], frame(pm, 0, 49)["grid_row"]) == (0, 49)   # end of first column
    assert (frame(pm, 0, 50)["grid_col"], frame(pm, 0, 50)["grid_row"]) == (1, 0)
    c = frame(pm, 13, 0)                                                              # scan row 1, col 1
    assert (c["grid_col"], c["grid_row"]) == (10, 50)
    assert frame(pm, 0, 500).get("grid_col") is None                                  # beyond the grid


def test_serpentine_and_wildcard(tmp_path):
    from api.positions import PositionMap
    pm = PositionMap(run(tmp_path, "--serpentine"))
    assert (frame(pm, 2, 50)["grid_col"], frame(pm, 2, 50)["grid_row"]) == (21, 49)
    assert frame(pm, 2, 50)["scan_counter"] == "2"
    other = {"ifile": "/data/x_12x_master.h5", "event": 0}    # must not match *_12_master.h5
    pm.fill(other)
    assert "grid_col" not in other


def test_masters_give_real_names(tmp_path):
    d = tmp_path / "grid_scans"
    d.mkdir()
    for n in range(24):
        (d / ("sbig-filtered-0-22_%d_master.h5" % n)).write_bytes(b"")
    rows = list(csv.DictReader(open(run(tmp_path, "--masters", str(d)))))
    assert rows[5]["ifile"] == "sbig-filtered-0-22_5_master.h5"


def test_plate_windows_become_wells(tmp_path):
    src = tmp_path / "plate_csv.txt"
    with open(src, "w") as f:
        w = csv.writer(f)
        w.writerow(['ScanID', 'Plate Name', 'Beam Energy', 'detector z', 'y speed', 'Window Name', 'window x',
                    'window y', 'Scan Counter', 'scan row', 'scan col', 'scan x', 'scan y'])
        for n, win in enumerate(["A1", "A2", "B1"]):
            w.writerow([1, 'p', 13, 0.2, 1, win, 0, 0, n, 0, 0, 0.0, 0.0])
    out = str(tmp_path / "p.csv")
    subprocess.run([sys.executable, SCRIPT, "--csv", str(src), "--grid-size", "50", "10", "--drop", "1", "-o", out],
                   check=True, capture_output=True)
    rows = list(csv.DictReader(open(out)))
    assert [r["well"] for r in rows] == ["A1", "A2", "B1"] and {r["drop"] for r in rows} == {"1"}


def test_patched_csv_needs_no_log(tmp_path):
    """csv written by the suggested SSXscanTools patch (docs/MX3_SCAN_SCRIPTS.md)."""
    src = tmp_path / "plate_csv.txt"
    with open(src, "w") as f:
        w = csv.writer(f)
        w.writerow(['ScanID', 'Plate Name', 'Beam Energy', 'detector z', 'y speed', 'Window Name', 'window x',
                    'window y', 'Scan Counter', 'scan row', 'scan col', 'scan x', 'scan y', 'spot',
                    'number_of_rows', 'number_of_columns', 'grid_width', 'grid_height', 'transmission', 'flux',
                    'beam_x_size', 'beam_y_size'])
        for n, (win, spot) in enumerate([("C3", 2), ("C3", 3)]):
            w.writerow([1, 'p', 13, 0.2, 1, win, 0, 0, n, 0, 0, 0.0, 0.0, spot, 40, 8, 84, 417, 0.05, 2.1e12, 6.4, 4.8])
    out = str(tmp_path / "p.csv")
    subprocess.run([sys.executable, SCRIPT, "--csv", str(src), "-o", out], check=True, capture_output=True)
    rows = list(csv.DictReader(open(out)))
    assert [(r["well"], r["drop"], r["grid_ny"], r["grid_nx"]) for r in rows] == [("C3", "2", "40", "8"), ("C3", "3", "40", "8")]
    assert rows[0]["flux"] == "2.1e+12" or float(rows[0]["flux"]) == 2.1e12
    assert rows[0]["transmission"] == "0.05"
