# DatView 2026: interactive analysis of million-pattern serial crystallography data sets, direct merging of selections, and sample-position maps for fixed-target and in-plate collection

*Draft, computer-program article (J. Appl. Cryst. format). Authors and affiliations: TBD.*

> Placeholders are marked **[TODO]**. Figures 2 and 3 currently use simulated or synthetic data and must be replaced or clearly labelled before submission.

## Synopsis

An update of *DatView* reduces memory use more than fivefold and keeps linked selections interactive with many plots open at 10⁶ rows. It merges any selection with CrystFEL without writing stream files, reads EIGER/NeXus metadata, and maps any per-frame parameter onto crystallisation plates and raster scans.

## Abstract

*DatView* (Stander *et al.*, 2019) is a graphical interface for exploring tabulated per-frame and per-crystal statistics from serial crystallography. Selections drawn on any plot are synchronised across all others. As synchrotron serial crystallography with fast-framing hybrid pixel detectors became routine, data sets reached 10⁶ patterns or more. At that size the original implementation became memory-bound and slowed progressively as plots were opened. We describe an update that keeps a single copy of the table, stores text columns as codes, releases plots when they are closed, coalesces redraws and rasterises dense scatter plots. At 10⁶ rows these changes reduce memory from 2.9–3.5 GB to 0.5 GB. A selection with 17 plots open updates in 2 s instead of 30 s. Any selection, or each partition of the data, can now be merged with *process_hkl* or *partialator* by streaming the selected chunks directly from the original stream files, so no intermediate stream file is written. *datgen* supports CrystFEL 0.10–0.12 streams and DECTRIS EIGER master-file metadata. A new plate view shows any parameter (unit-cell axis, peak count, indexing rate, resolution) per well, drop and raster point of 24- and 96-well plates or fixed targets. Selections on the plate view are linked to all other plots. The software is open source (https://github.com/nzatsepin/DatView).

**Keywords:** serial crystallography; data visualisation; CrystFEL; fixed target; graphical user interfaces.

## 1. Introduction

Serial crystallography merges single diffraction snapshots from many crystals. Before merging, the analyst must decide which patterns to include, and find systematic effects in sample delivery, detector or processing. DatView (Stander *et al.*, 2019) addressed this by reducing CrystFEL (White *et al.*, 2012, 2016) stream files to a table of per-frame and per-crystal parameters. Every plot of that table shares a single selection, and any subset can be exported as a stream for merging.

Two developments motivated this update. First, serial crystallography at synchrotrons with EIGER-class detectors routinely produces 10⁵–10⁶ indexed patterns per experiment. DatView was usable at these sizes only with few plots open, and it became progressively slower in use. Second, fixed-target and in-plate collection (raster scans across drops in crystallisation plates or patterned chips) makes sample position a primary variable. Dehydration or humidity gradients, crystal density, and differences between drops and wells appear as spatial patterns in unit-cell parameters, hit rate and resolution. The original paper showed this for unit-cell variation across patterned silicon chips (Stander *et al.*, 2019, Fig. 5), but position mapping required external scripts.

## 2. Implementation

### 2.1 Memory and responsiveness

Profiling identified four causes of high memory use and slow interaction:

1. **Plot lifecycle.** Each plot connected to the data model's selection signals and never disconnected. Closed windows and unticked histograms remained alive and kept redrawing on every selection change, so the cost of a selection grew with the number of plots ever opened. Plots now record their connections and release them, together with the figure, when closed.
2. **Duplicate table.** The table was held both as read (including fixed-width Unicode copies of file paths) and as converted. Only one numeric table is now kept. Text columns are stored as integer codes into a sorted label array and parsed directly as categories. Original values are retained only for the few columns rescaled on load, so exported tables reproduce the input exactly.
3. **Eager selection copies.** The selected rows were copied on every selection change. Plots use boolean masks; the copy is now built only when an export needs it.
4. **Rendering.** Histograms are drawn as one filled step outline per stack instead of one rectangle per bin. Above a configurable threshold (default 5 × 10⁴ points), scatter plots are drawn as an image binned at screen resolution within the current view and re-binned on zoom. Redraws triggered by the several signals of one selection change are merged, and hidden plots defer redrawing until shown.

The structured numpy table, the XML configuration, filters, groups, partitions and the scripts were kept, so existing tables, filter files and workflows remain valid. Columnar data-frame engines were evaluated and not adopted. At 10⁶ rows, boolean filtering of numpy columns takes ~1 ms, so the cost lay in plotting and in plots that were never released.

### 2.2 Merging without intermediate stream files

For each row, *datgen* records the byte offsets of the chunk, crystal, peak list and reflection list in the source stream. The update uses these offsets to read the selected chunks directly from the original streams in binary mode. Each selected crystal is written as a separate chunk (chunk header and peak list, followed by that crystal). The resulting stream is passed on standard input to *process_hkl* or *partialator* (`-i -`). This avoids writing, storing and re-reading a subset stream, which is often tens of gigabytes. With partitions active (*e.g.* by well, time point or detector distance), one merge runs per partition. The *process_hkl* output for a piped selection was identical to that obtained from the same selection exported as a file.

### 2.3 Current CrystFEL streams and EIGER data

*datgen* was rewritten as a single-pass parser for streams from CrystFEL 0.10–0.12. The 2019 column order is preserved, and fields introduced since are added (image serial number, hit flag, number of indexing attempts, peak resolution, unique axis, prediction-refinement residual). Values copied into the stream with `indexamajig --copy-header` can be added as columns. Arbitrary HDF5 datasets, including the standard NXmx paths of DECTRIS master files, can be read per frame from the image files. The frame viewer follows the external links of EIGER master files and decompresses bitshuffle/LZ4 data through *hdf5plugin*.

### 2.4 Sample-position maps

A position table assigns each frame (by image file and event range) to a well, drop and raster point. It can be written by the data-collection script, one line per scan. Raster indices follow from the frame number, the raster dimensions and the scan direction (including serpentine scans). The plate view aggregates any column per raster point, drop or well (mean, median, extrema, standard deviation, count or fraction of frames with a positive value, *e.g.* indexing rate). It lays out the aggregates on the plate grid. Selections on the plate view are linked to all other plots, and the colour scale is fixed to the full data set so that colours stay comparable while the selection changes (Fig. 2).

## 3. Performance

Tables of 2 × 10⁵ and 10⁶ rows were generated by replicating rows of real CrystFEL 0.12 stream output (47 columns). Both versions ran on the same two-core virtual machine (Table 1, Fig. 1). The test loaded the table with 12 linked histograms, applied a selection, opened five scatter plots, applied a selection, closed the scatter plots and applied a further selection.

**Table 1.** Memory (resident, above baseline) and time per selection change, 10⁶ rows.

| | 2019 | 2026 |
|---|---|---|
| Load | 17.7 s | 6.2 s |
| Memory, 12 histograms | 2.92 GB | 0.55 GB |
| Memory after opening and closing 5 scatter plots | 3.54 GB | 0.50 GB |
| Peak memory | 5.04 GB | 0.92 GB |
| Selection, 12 histograms | 1.3 s | 0.9 s |
| Selection, 12 histograms + 5 scatter plots | 29.7 s | 2.0 s |
| Selection after closing the scatter plots | 31.9 s | 0.8 s |

**Figure 1.** Memory and selection time, 10⁶ rows (`docs/img/benchmark_1M.png`).

**Figure 2.** Plate view of a 96-well, two-drop raster data set. **[TODO: replace the synthetic example (`docs/img/plate_demo.png`) with an MX3 in-plate data set: mean unit-cell *a* per drop, and indexing rate per raster point.]**

**Figure 3.** **[TODO: example of selecting the edge wells on the plate view and the corresponding shift in the unit-cell histograms, with merged-data statistics per partition from §2.2.]**

## 4. Availability and requirements

Source: https://github.com/nzatsepin/DatView **[TODO: licence. The repository has no LICENSE file. The bundled CfelPyUtils geometry code is GPL-3.0, so GPL-3.0 is the compatible choice.]**. Requirements: Python ≥ 3.9, numpy, scipy, matplotlib, h5py, hdf5plugin, lxml, PyQt5, pyqtgraph; pandas recommended. Merging requires CrystFEL ≥ 0.10 on the user's path. A test suite based on CrystFEL 0.12 output and the benchmark script are included.

## Acknowledgements

**[TODO]**

## References

Stander, N., Fromme, P. & Zatsepin, N. A. (2019). *J. Appl. Cryst.* **52**, 1440–1448. https://doi.org/10.1107/S1600576719012044

White, T. A., Kirian, R. A., Martin, A. V., Aquila, A., Nass, K., Barty, A. & Chapman, H. N. (2012). *J. Appl. Cryst.* **45**, 335–341. https://doi.org/10.1107/S0021889812002312

White, T. A., Mariani, V., Brehm, W., Yefanov, O., Barty, A., Beyerlein, K. R., Chervinskii, F., Galli, L., Gati, C., Nakane, T., Tolstikova, A., Yamashita, K., Yoon, C. H., Diederichs, K. & Chapman, H. N. (2016). *J. Appl. Cryst.* **49**, 680–689. https://doi.org/10.1107/S1600576716004751

White, T. A. (2019). *Acta Cryst.* D**75**, 219–233. https://doi.org/10.1107/S205979831801238X
