#!/usr/bin/env python3
"""
Simulated EIGER-style serial crystallography test data.

Writes, into an output directory:
  sim_data_000001.h5   frames, /entry/data/data (N, ss, fs), bitshuffle-LZ4 if available
  sim_master.h5        Dectris-like NeXus master: external link to the data file plus
                       detector/beam/goniometer metadata (scalars and one per-frame array)
  sim.geom             CrystFEL geometry for the master file
  sim.cell             CrystFEL unit cell (tetragonal, lysozyme-like)
  frames.lst           master file list for indexamajig

The diffraction model is deliberately simple (spot positions from the Ewald construction,
Gaussian spot profiles, Poisson background). It exists to give indexamajig something real
to index so the tests run against a genuine CrystFEL stream, not to model physics.
"""

import argparse
import os
import numpy as np
import h5py

try:
    import hdf5plugin
    COMPRESSION = hdf5plugin.Bitshuffle(cname="lz4")
except ImportError:
    COMPRESSION = {}

# ---------------------------------------------------------------------------
# Experiment constants
# ---------------------------------------------------------------------------
NFS, NSS = 1024, 1024
PIXEL = 75e-6            # m
CLEN = 0.10              # m
WAVELENGTH = 1.0         # Angstrom
CELL = (79.1, 79.1, 38.0)  # Angstrom, tetragonal P
DMIN = 1.9               # Angstrom
EXCITATION = 0.006      # 1/Angstrom, half-width of the Ewald shell accepted as a spot


def random_rotation(rng):
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
                     [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                     [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]])


def spots_for_lattice(rot, rng):
    """Return (fs, ss, intensity) of reflections in diffraction condition."""
    a, b, c = CELL
    hmax = int(a / DMIN) + 1
    kmax = int(b / DMIN) + 1
    lmax = int(c / DMIN) + 1
    h, k, l = np.meshgrid(np.arange(-hmax, hmax + 1), np.arange(-kmax, kmax + 1),
                          np.arange(-lmax, lmax + 1), indexing="ij")
    hkl = np.stack([h.ravel(), k.ravel(), l.ravel()], axis=1)
    hkl = hkl[np.any(hkl != 0, axis=1)]
    g = hkl / np.array(CELL)                 # reciprocal vectors, crystal frame (1/A)
    g = g[np.linalg.norm(g, axis=1) < 1 / DMIN] @ rot.T
    k0 = np.array([0, 0, 1 / WAVELENGTH])
    kout = k0 + g
    err = np.linalg.norm(kout, axis=1) - 1 / WAVELENGTH
    ok = (np.abs(err) < EXCITATION) & (kout[:, 2] > 0)
    kout = kout[ok]
    x = kout[:, 0] / kout[:, 2] * CLEN / PIXEL
    y = kout[:, 1] / kout[:, 2] * CLEN / PIXEL
    fs = x + NFS / 2
    ss = y + NSS / 2
    inside = (fs > 5) & (fs < NFS - 5) & (ss > 5) & (ss < NSS - 5)
    inten = rng.exponential(1500.0, size=inside.sum()) + 300
    return fs[inside], ss[inside], inten


def render(spots, rng):
    img = rng.poisson(3.0, size=(NSS, NFS)).astype(np.float64)
    yy, xx = np.mgrid[-3:4, -3:4]
    for fs, ss, inten in spots:
        for f, s, i in zip(fs, ss, inten):
            fi, si = int(round(f)), int(round(s))
            prof = np.exp(-((xx - (f - fi))**2 + (yy - (s - si))**2) / (2 * 0.8**2))
            img[si - 3:si + 4, fi - 3:fi + 4] += rng.poisson(i * prof / prof.sum())
    return img.astype(np.uint32)


def write_geometry(path, data_path):
    with open(path, "w") as g:
        g.write(f"""; Simulated single-panel detector for DatView tests
wavelength = {WAVELENGTH} A
clen = {CLEN}
res = {1/PIXEL:.4f}
adu_per_photon = 1

dim0 = %
dim1 = ss
dim2 = fs
data = {data_path}

p0/min_fs = 0
p0/min_ss = 0
p0/max_fs = {NFS-1}
p0/max_ss = {NSS-1}
p0/corner_x = {-NFS/2}
p0/corner_y = {-NSS/2}
p0/fs = x
p0/ss = y
""")


def write_cell(path):
    a, b, c = CELL
    with open(path, "w") as f:
        f.write(f"""CrystFEL unit cell file version 1.0

lattice_type = tetragonal
centering = P
unique_axis = c

a = {a} A
b = {b} A
c = {c} A
al = 90.0 deg
be = 90.0 deg
ga = 90.0 deg
""")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("outdir")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    # Frame mix: ~20% blanks, ~15% two lattices, rest single lattice
    kinds = rng.choice([0, 1, 2], size=args.frames, p=[0.2, 0.65, 0.15])
    frames = []
    for kind in kinds:
        spots = [spots_for_lattice(random_rotation(rng), rng) for _ in range(kind)]
        frames.append(render(spots, rng))
    stack = np.stack(frames)

    data_name = "sim_data_000001.h5"
    with h5py.File(os.path.join(args.outdir, data_name), "w") as f:
        f.create_dataset("/entry/data/data", data=stack, chunks=(1, NSS, NFS), **COMPRESSION)

    with h5py.File(os.path.join(args.outdir, "sim_master.h5"), "w") as m:
        m["/entry/data/data_000001"] = h5py.ExternalLink(data_name, "/entry/data/data")
        det = m.create_group("/entry/instrument/detector")
        det["detector_distance"] = CLEN
        det["beam_center_x"] = NFS / 2
        det["beam_center_y"] = NSS / 2
        det["count_time"] = 0.0099
        det["frame_time"] = 0.01
        det["x_pixel_size"] = PIXEL
        det["y_pixel_size"] = PIXEL
        det["detectorSpecific/nimages"] = args.frames
        m["/entry/instrument/beam/incident_wavelength"] = WAVELENGTH
        m["/entry/sample/goniometer/omega"] = np.linspace(0, 0.1 * (args.frames - 1), args.frames)

    write_geometry(os.path.join(args.outdir, "sim.geom"), "/entry/data/data_000001")
    write_cell(os.path.join(args.outdir, "sim.cell"))
    with open(os.path.join(args.outdir, "frames.lst"), "w") as f:
        f.write(os.path.abspath(os.path.join(args.outdir, "sim_master.h5")) + "\n")
    print("frames:", args.frames, "blank:", int((kinds == 0).sum()),
          "single:", int((kinds == 1).sum()), "double:", int((kinds == 2).sum()))


if __name__ == "__main__":
    main()
