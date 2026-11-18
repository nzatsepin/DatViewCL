"""Table generation, loading, export and merging, checked against real CrystFEL 0.12 streams."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from conftest import make_dat, STREAMS, DATA

pd = pytest.importorskip("pandas")


def read(path):
    return pd.read_csv(path, sep=r"\s+", index_col=False)


def test_datgen_help_runs():
    import datgen
    with pytest.raises(SystemExit) as e:
        datgen.main(["-h"])
    assert e.value.code == 0


def test_rows_and_offsets(tmp_path):
    d = read(make_dat(tmp_path))
    chunks = sum(open(s).read().count("----- Begin chunk -----") for s in STREAMS)
    extra = int((d.multiid > 1).sum())
    assert len(d) == chunks + extra
    for _, r in d.iterrows():
        data = open(r.sfile, "rb").read()
        assert data[r.istart:].startswith(b"----- Begin chunk -----")
        assert data[:r.iend].endswith(b"----- End chunk -----\n")
        if r.cstart >= 0:
            assert data[r.cstart:].startswith(b"--- Begin crystal")
            assert data[:r.cend].endswith(b"--- End crystal\n")
            assert data[r.rend:].startswith(b"End of reflections")
        if r.pstart >= 0:
            assert data[r.pend:].startswith(b"End of peak list")


def test_new_stream_fields(tmp_path):
    d = read(make_dat(tmp_path, extra=["--headers"]))
    assert set(d.hit) <= {0, 1}
    assert (d.serial > 0).all()
    indexed = d[d.multi > 0]
    assert (indexed.ntries >= 1).all() and (indexed.uaxis == "c").all()
    assert "/entry/sample/goniometer/omega" in d.columns
    assert np.allclose(d["/entry/sample/goniometer/omega"], d.event * 0.1, atol=1e-6)


def test_dat_roundtrip_and_npz(tmp_path):
    from api.datamodel import DataModel
    src = make_dat(tmp_path)
    m = DataModel(src)
    assert "rdata" not in m.__dict__              # no duplicate raw table
    assert set(m.rawcols) == {"a", "b", "c"}      # only columns converted on load
    assert np.allclose(m.data["a"][m.data["a"] != -1], read(src).a[read(src).a != -1] * 10, rtol=1e-6)
    out = str(tmp_path / "rt.dat")
    m.saveSelDat(out)
    a, b = read(src), read(out)
    for c in a.columns:
        if pd.api.types.is_numeric_dtype(a[c]):
            assert np.allclose(a[c], b[c], rtol=2e-6, atol=1e-7), c
        else:
            assert (a[c] == b[c]).all(), c
    npz = str(tmp_path / "t.npz")
    m.saveAllNumpy(npz)
    m2 = DataModel(npz)
    out2 = str(tmp_path / "rt2.dat")
    m2.saveSelDat(out2)
    assert open(out).read() == open(out2).read()


def test_legacy_2019_dat_loads(tmp_path):
    """Tables written by the 2019 datgen (trailing tab, fewer columns) still load."""
    from api.datamodel import DataModel
    src = read(make_dat(tmp_path))
    old = src[[c for c in src.columns if c not in ("serial", "hit", "ntries", "pkres", "uaxis", "presid")]]
    path = tmp_path / "old.dat"
    with open(path, "w") as f:
        f.write("\t".join(old.columns) + "\n")
        for row in old.astype(str).values:
            f.write("\t".join(row) + "\t\n")
    m = DataModel(str(path))
    assert len(m.data) == len(old)


def test_stream_export_is_valid_and_merges(tmp_path):
    from api.datamodel import DataModel
    m = DataModel(make_dat(tmp_path))
    out = str(tmp_path / "sel.stream")
    m.saveSelStream(out)
    text = open(out).read()
    assert text.startswith("CrystFEL stream format")
    assert text.count("----- Begin chunk -----") == len(m.data)
    assert text.count("--- Begin crystal") == int((m.data["multi"] > 0).sum())
    if shutil.which("process_hkl") is None:
        pytest.skip("process_hkl not on PATH")
    f1, f2 = str(tmp_path / "file.hkl"), str(tmp_path / "pipe.hkl")
    subprocess.run(["process_hkl", "-i", out, "-o", f1, "-y", "4/mmm"], check=True, capture_output=True)
    assert m.pipeSelStream("process_hkl -y 4/mmm -o %s" % f2) == 0
    body = lambda p: [l for l in open(p) if not l.startswith("process_hkl")]
    assert body(f1) == body(f2)


def test_partialator_reads_pipe(tmp_path):
    """partialator reads the piped selection exactly as it reads the same stream from a file.

    (The simulated crystals are all rejected by partialator's profile-radius check, so the
    comparison is on what it reports reading rather than on a merged output.)"""
    if shutil.which("partialator") is None:
        pytest.skip("partialator not on PATH")
    from api.datamodel import DataModel
    from api import streamio
    m = DataModel(make_dat(tmp_path))
    out = str(tmp_path / "sel.stream")
    m.saveSelStream(out)
    args = ["partialator", "-y", "4/mmm", "--model=unity", "--iterations=1", "--no-logs", "-o", str(tmp_path / "p.hkl")]
    a = subprocess.run(args + ["-i", out], capture_output=True, text=True, cwd=str(tmp_path))
    b = subprocess.run(args + ["-i", "-"], input=open(out).read(), capture_output=True, text=True, cwd=str(tmp_path))
    seen = lambda r: sorted(l for l in r.stderr.splitlines() if l.startswith("Rejecting"))
    assert a.returncode == b.returncode
    assert seen(a) == seen(b) and len(seen(a)) == int((m.data["multi"] > 0).sum())


def test_merge_by_partitions(tmp_path):
    from api.datamodel import DataModel
    from api import streamio
    m = DataModel(make_dat(tmp_path))
    calls = []
    streamio_pipe = streamio.pipe_selection
    try:
        streamio.pipe_selection = lambda rows, cmd, progress=None: calls.append((cmd, len(list(rows)))) or 0
        parts = m.partition("multi")
        res = m.mergeByPartitions("process_hkl -y 4/mmm -o out.hkl", parts)
    finally:
        streamio.pipe_selection = streamio_pipe
    assert set(res) == set(parts)
    assert sum(n for _, n in calls) == len(m.data)
    assert all("out_" in c for c, _ in calls)


def test_merge_command_forces_stdin():
    from api.streamio import merge_command
    assert merge_command("process_hkl -i old.stream -y 2/m -o x.hkl") == ["process_hkl", "-y", "2/m", "-o", "x.hkl", "-i", "-"]
    assert merge_command("partialator --input=a.stream -y 2/m") == ["partialator", "-y", "2/m", "-i", "-"]


def test_positions_and_grid(tmp_path):
    d = read(make_dat(tmp_path, positions=True))
    r = d[d.event == 5].iloc[0]                    # second raster row, snake: reversed
    assert (r["well"], r["drop"], r["grid_col"], r["grid_row"]) == ("A1", 1, 4, 1)
    r = d[d.event == 58].iloc[0]
    assert tuple(r[["well", "well_row", "well_col", "drop", "grid_col", "grid_row"]]) == ("B1", 2, 1, 2, 1, 1)


def test_grid_option_without_table(tmp_path):
    d = read(make_dat(tmp_path, extra=["--grid", "10", "6"]))
    assert ((d.grid_row * 10 + d.grid_col) == d.event).all()


def test_h5_fields_scalar_array_missing(tmp_path):
    h5py = pytest.importorskip("h5py")
    import datgen
    master = tmp_path / "m_master.h5"
    with h5py.File(master, "w") as f:
        f["/entry/instrument/detector/detector_distance"] = 0.2
        f["/entry/sample/goniometer/omega"] = np.arange(5) * 0.5
    lst = tmp_path / "f.lst"
    lst.write_text("".join("%s //%d\n" % (master, i) for i in range(5)))
    out = tmp_path / "l.dat"
    datgen.main(["-o", str(out), "--eiger", str(lst)])
    d = read(out)
    assert np.allclose(d["/entry/instrument/detector/detector_distance"], 0.2)
    assert np.allclose(d["/entry/sample/goniometer/omega"], np.arange(5) * 0.5)
    assert (d["/entry/instrument/detector/beam_center_x"] == -1).all()
