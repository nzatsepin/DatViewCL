# Changelog

## 2026.1

### Added
- Merge the selection directly with `process_hkl`/`partialator` (stdin), from the GUI (*File → Merge Selection*) or `datexport.py --merge`. One merge per partition.
- Plate / sample position plot (24/96-well, drops, raster points). Linked selection. `datplot.py` plot type `plate`.
- `datgen.py`: CrystFEL 0.10–0.12 fields (`serial`, `hit`, `ntries`, `pkres`, `uaxis`, `presid`). New options `--eiger`, `--h5`, `--headers`/`--header`, `--positions`, `--grid`/`--serpentine`.
- Item viewer reads EIGER master files through `data_NNNNNN` links (bitshuffle via `hdf5plugin`).
- `scripts/makepositions.py`, `scripts/h5fields.py`, `scripts/mx3plate2positions.py` (MX3 SSX notebook plate csv/log → positions).
- Positions tables: `fast_axis`, `grid_col_offset`/`grid_row_offset`, wildcard `ifile`. `scripts/listevents.py` rewritten for EIGER masters.
- Raster drawing of large scatter plots (`scatterRasterThreshold`). `platecmap` config option.
- Memory readout in the status bar.
- `requirements.txt` and a test suite (`tests/`).
- Tutorial in Markdown (`docs/tutorial/`), from the 2019 Confluence tutorial.

### Changed
- Large tables load about 3× faster and use about 5× less memory.
- Selections stay fast with many plots open. Closing a plot window or unticking a histogram frees it.
- Histograms are drawn as step outlines (counts unchanged).
- `.npz` files are smaller. Old `.npz` files still load.

### Fixed
- *OR* filters in filter files raised an exception.
- `aclen` exported as an integer. Orientation-matrix columns exported to 6 decimals.
- numpy 2 / matplotlib ≥ 3.9 incompatibilities (`np.int`, `asstr`, `get_cmap`, `savefig(ext=)`, `is not -1`).
- `datplot.py -s` created the figure before any QApplication existed. A float window size crashed on new PyQt5.
- `datcompare.py` on numpy 2.
- With pandas ≥ 3, text columns not declared in the config failed to load.

## 2019
Original release, designed and written by Natasha Stander. See the 2019 manual and Stander, Fromme & Zatsepin, J. Appl. Cryst. 52 (2019) 1440.
