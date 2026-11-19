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
- `requirements.txt`, test suite (`tests/`), benchmark (`tests/benchmark.py`).

### Changed
- Single in-memory table. Text columns are stored as codes. Lazy selection copy. 5× lower memory at 10⁶ rows.
- Closed plot windows and unticked histograms are destroyed and disconnected from the model.
- Redraws are coalesced. Hidden plots do not redraw.
- Histograms drawn as step patches.
- Stream export and peak/reflection reading use binary I/O.
- `.npz` files no longer store a raw copy of the table. Old `.npz` files still load.

### Fixed
- *OR* filters in filter files raised an exception.
- `aclen` exported as an integer. Orientation-matrix columns exported to 6 decimals.
- numpy 2 / matplotlib ≥ 3.9 incompatibilities (`np.int`, `asstr`, `get_cmap`, `savefig(ext=)`, `is not -1`).
- `datplot.py -s` created the figure before any QApplication existed. A float window size crashed on new PyQt5.
- `datcompare.py` on numpy 2.
- With pandas ≥ 3, text columns not declared in the config failed to load.

## 2019
Original release by Natasha Stander. See the 2019 manual and Stander, Fromme & Zatsepin, J. Appl. Cryst. 52 (2019) 1440.
