# DatView 2026: what's new

DatView was designed and written by Natasha Stander (Zatsepin lab, 2017–2019). The 2026 version keeps her interface, file formats and workflow, and updates the code for current Python and CrystFEL.

## For users

- **Faster, lighter.** Large tables load about 3× faster and use about 5× less memory. Selections stay fast with many plots open, and closing a plot frees its memory.
- **Merge a selection directly** with `process_hkl` or `partialator`, from *File → Merge Selection* or `datexport.py --merge`. No intermediate stream file.
- **CrystFEL 0.10–0.12 streams**, including the newer per-frame and per-crystal fields.
- **EIGER / HDF5 master files**: detector and beam metadata in the table, and frames in the item viewer.
- **Plate / sample-position plot** for 24- and 96-well plates, drops and raster scans, linked to all other plots.
- **Current Python**: 3.9–3.13, numpy 2, current matplotlib and pandas.
- **Compatible**: 2019 `.dat`, `.npz`, filter, group and configuration files still load.

## Updating

```bash
cd DatViewCL && git pull && pip install -r requirements.txt
```

## Documentation

- [Tutorial](tutorial/README.md)
- [Manual: 2026 additions](MANUAL_2026.md)
- [Manual and tutorial (2019, PDF)](DatView_Manual_and_Tutorial_2019.pdf)
- [Changelog](../CHANGELOG.md)
