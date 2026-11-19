"""Shared fixtures: dat tables generated from the small CrystFEL 0.12 streams in tests/data."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
DATA = os.path.join(HERE, "data")
STREAMS = [os.path.join(DATA, "small1.stream"), os.path.join(DATA, "small2.stream")]

POSITIONS = """ifile,event_start,event_end,well,drop,grid_nx,grid_ny,serpentine,condition
sim_master.h5,0,9,A1,1,5,2,yes,PEG 20%
sim_master.h5,10,19,A1,2,5,2,yes,PEG 25%
sim_master.h5,20,29,A2,1,5,2,yes,PEG 25%
sim_master.h5,30,39,A2,2,5,2,yes,PEG 25%
sim_master.h5,40,49,B1,1,5,2,yes,PEG 25%
sim_master.h5,50,59,B1,2,5,2,yes,PEG 25%
"""


def make_dat(tmp_path, positions=False, extra=()):
    import datgen
    out = os.path.join(str(tmp_path), "test%s.dat" % ("_pos" if positions else ""))
    args = ["-o", out]
    if positions:
        pfile = os.path.join(str(tmp_path), "positions.csv")
        with open(pfile, "w") as f:
            f.write(POSITIONS)
        args += ["--positions", pfile]
    datgen.main(args + list(extra) + STREAMS)
    return out
