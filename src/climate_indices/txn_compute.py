#!/usr/bin/env python3
"""
txn_compute.py
---------------------------------------------------------------
TXn: Coldest daily maximum temperature (°C).

For each model and time slice:
  1) Convert tasmax to °C
  2) For each year: TXn_year = min(tasmax) (annual min of daily Tmax)
  3) Slice mean = mean(TXn_year) -> one value per grid cell (days removed)
  4) Ensemble-average across models

Inputs (combined, one file per model+experiment):
  data/combined/tasmax/<EXPERIMENT>/tasmax_<MODEL>_<EXPERIMENT>.nc
Outputs:
  data/outputs/txn/txn_ensemble_mean_<experiment>_<slice>.nc
"""

from __future__ import annotations
import os, re, glob, time, warnings
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

warnings.filterwarnings("ignore")
# Safer defaults for shared filesystems & CF calendars
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("XR_USE_FLOX", "0")

try:
    import dask
    dask.config.set({
        "array.slicing.split_large_chunks": True,
        "scheduler": "single-threaded",   # avoid parallel segfaults in IO stack
    })
except Exception:
    pass

# ----------------- helpers ----------------- #

def _label_slug(s: str) -> str:
    return (s.lower()
              .replace(" ", "_")
              .replace("(", "").replace(")", "")
              .replace("–", "-").replace("/", "-"))

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _open_single(path: Path) -> xr.Dataset:
    """
    Open a single combined file with robust fallbacks.
    No multi-file open (prevents many segfault scenarios).
    """
    # Try h5netcdf + cftime first (fast + calendar-safe)
    for kw in [
        dict(engine="h5netcdf", use_cftime=True, decode_times=True, chunks={"time": -1}, mask_and_scale=True, lock=False),
        dict(engine="scipy",     use_cftime=True, decode_times=True, chunks={"time": -1}),
        dict(engine="scipy",     use_cftime=True, decode_times=False, chunks={"time": -1}),  # decode later
    ]:
        try:
            ds = xr.open_dataset(str(path), **kw)
            # If we had decode_times=False, try to decode now
            if not kw.get("decode_times", True):
                try:
                    ds = xr.decode_cf(ds)
                except Exception:
                    pass
            return ds
        except Exception:
            continue
    raise IOError(f"Could not open: {path}")

def _year_regex():
    return re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

def _year_from_name(p: Path) -> Optional[int]:
    m = _year_regex().search(p.name);  return int(m.group()) if m else None

def _find_model_name(p: Path, experiment: str) -> str:
    # Expect combined: .../combined/tasmax/<experiment>/tasmax_<MODEL>_<experiment>.nc
    try:
        idx = p.parts.index(experiment)
        if idx > 0:  # model often in filename rather than path here
            name = p.stem.replace("tasmax_", "").replace(f"_{experiment}", "")
            return name or "unknown_model"
    except ValueError:
        pass
    toks = p.stem.split("_")
    # tasmax_<MODEL>_<EXPERIMENT>
    return toks[1] if len(toks) >= 3 else "unknown_model"

def _models_in_files(files: List[Path], experiment: str) -> List[str]:
    return sorted({_find_model_name(p, experiment) for p in files})

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    """Slice using the dataset's own time dtype/calendar."""
    if "time" not in ds or ds.sizes.get("time", 0) == 0:
        return ds
    t0 = ds["time"].values[0]
    # Build start/end in the same calendar/dtype as t0
    if isinstance(t0, np.datetime64):
        s = np.datetime64(start); e = np.datetime64(end)
    else:
        y0,m0,d0 = int(start[:4]), int(start[5:7]), int(start[8:10])
        y1,m1,d1 = int(end[:4]),   int(end[5:7]),   int(end[8:10])
        try:
            s = type(t0)(y0,m0,d0); e = type(t0)(y1,m1,d1)
        except TypeError:
            import cftime
            s = cftime.DatetimeProlepticGregorian(y0,m0,d0)
            e = cftime.DatetimeProlepticGregorian(y1,m1,d1)
    tmin = ds["time"].values[0]; tmax = ds["time"].values[-1]
    s = s if s > tmin else tmin
    e = e if e < tmax else tmax
    if e < s:
        return ds.isel(time=slice(0, 0))
    return ds.sel(time=slice(s, e))

# ----------------- main ----------------- #

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXn (coldest daily maximum temperature) — single-file-per-model path…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg      = cfg.get("txn", {})
    varname   = icfg.get("variable", "tasmax")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths (combined inputs)
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txn"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory per experiment (each file is one model)
    exp_files: Dict[str, List[Path]] = {
        exp: sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"{exp}/*.nc"), recursive=False))
        for exp in experiments
    }

    for experiment, files in exp_files.items():
        print(f"\n📁 Experiment: {experiment}  ({len(files)} files)")
        if not files:
            continue

        # For historical compute only the Baseline slice; for SSPs, all non-baseline
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.lower().startswith("baseline")]

        models = _models_in_files(files, experiment)
        print(f"   Models detected: {len(models)}")

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            if not start or not end:
                continue
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for f in files:
                model = _find_model_name(f, experiment)

                try:
                    # Open single file safely (no mfdataset, no parallel IO)
                    with _open_single(f) as ds:
                        # quick sanity
                        if varname not in ds:
                            print(f"   ⚠️ {model}: '{varname}' missing in {f.name}; skipping.")
                            continue

                        ds = _select_bbox(ds, lat_bounds, lon_bounds)
                        ds = _slice_time_cf(ds, start, end)
                        if ds.sizes.get("time", 0) == 0:
                            continue

                        tmax = convert_units_to(ds[varname], "degC")

                        # Annual TXn: min of daily Tmax per-year, then mean across years
                        # Resample avoids Python loops and keeps the compute graph small
                        annual_txn = tmax.resample(time="YS").min(dim="time")
                        if annual_txn.sizes.get("time", 0) == 0:
                            continue

                        txn_mean = annual_txn.mean(dim="time").astype("float32").load()

                    # accumulate ensemble
                    if ens_sum is None:
                        ens_sum = txn_mean.copy()
                        ens_n = 1
                    else:
                        # align grids if needed
                        if set(txn_mean.dims) != set(ens_sum.dims) or any(
                            txn_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in txn_mean.dims
                        ):
                            txn_mean = txn_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + txn_mean
                        ens_n += 1

                    used_models.append(model)

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TXn outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"txn_ensemble_mean_{experiment}_{label}.nc"

            xr.Dataset(
                {"txn": ensemble_mean.assign_attrs({
                    "units": "degC",
                    "long_name": "Coldest daily maximum temperature (TXn)",
                    "definition": "Mean of annual minima of daily tasmax within the time slice",
                })},
                attrs={
                    "title": f"TXn Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "created_by": "txn_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")