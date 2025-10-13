#!/usr/bin/env python3
"""
txge30_compute.py (single-file-per-model, cftime-safe)
---------------------------------------------------------------
TXge30: Mean number of days per year with tasmax >= 30 °C
computed per model and time slice, then ensemble-averaged.

Inputs (combined; one file per model+experiment):
  data/combined/tasmax/<experiment>/tasmax_<MODEL>_<experiment>.nc

Outputs:
  data/outputs/txge30/txge30_ensemble_mean_<experiment>_<slice>.nc

Config keys expected (YAML -> cfg):
  region.lat_min, region.lat_max, region.lon_min, region.lon_max
  experiments.select
  time_slices:
    Baseline (1995–2014): ["1995-01-01", "2014-12-31"]
    ...
  txge30:
    variable: tasmax
    threshold_c: 30.0
"""

from __future__ import annotations
import os, time, glob, warnings
from pathlib import Path
from typing import Dict, Tuple, List

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

# ───────────── runtime guards (reduce segfault/lock issues) ─────────────
warnings.filterwarnings("ignore")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("XR_USE_FLOX", "0")  # safer groupby behavior

try:
    import dask
    dask.config.set({
        "array.slicing.split_large_chunks": True,
        "scheduler": "single-threaded",  # avoids HDF5+threads crashes
    })
except Exception:
    pass

# ───────────── helpers ─────────────

def _label_slug(s: str) -> str:
    return (s.lower().replace(" ", "_").replace("(", "").replace(")", "")
                 .replace("–", "-").replace("/", "-"))

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    if "lat" not in ds.coords or "lon" not in ds.coords:
        return ds
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _open_single(path: Path) -> xr.Dataset:
    """Open one combined file robustly (no mfdataset, cftime-safe)."""
    for kw in [
        dict(engine="h5netcdf", use_cftime=True, decode_times=True, chunks={"time": -1}, mask_and_scale=True, lock=False),
        dict(engine="scipy",     use_cftime=True, decode_times=True, chunks={"time": -1}),
        dict(engine="scipy",     use_cftime=True, decode_times=False, chunks={"time": -1}),  # decode later
    ]:
        try:
            ds = xr.open_dataset(str(path), **kw)
            if not kw.get("decode_times", True):
                try:
                    ds = xr.decode_cf(ds)
                except Exception:
                    pass
            return ds
        except Exception:
            continue
    raise IOError(f"Could not open: {path}")

def _model_from_filename(p: Path, experiment: str) -> str:
    # Expect: tasmax_<MODEL>_<experiment>.nc
    toks = p.stem.split("_")
    if len(toks) >= 3 and toks[-1] == experiment:
        return "_".join(toks[1:-1])
    # Fallback: folder before experiment
    try:
        idx = p.parts.index(experiment)
        if idx > 0:
            return p.parts[idx - 1]
    except ValueError:
        pass
    return "unknown_model"

def _list_models(exp_dir: Path, experiment: str) -> Dict[str, Path]:
    """Return {model: file} for combined files under a given experiment dir."""
    mapping = {}
    if not exp_dir.exists():
        return mapping
    for f in sorted(exp_dir.glob("*.nc")):
        m = _model_from_filename(f, experiment)
        mapping[m] = f
    return mapping

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    """Slice with the dataset's own calendar (handles NoLeap/360d/etc.)."""
    if "time" not in ds or ds.sizes.get("time", 0) == 0:
        return ds
    t0 = ds["time"].values[0]
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

# ───────────── main ─────────────

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXge30 (days/year with TX ≥ threshold) — single-file, cftime-safe…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg      = cfg.get("txge30", {})
    varname   = icfg.get("variable", "tasmax")
    thr_c     = float(icfg.get("threshold_c", 30.0))

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths (combined inputs)
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txge30"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory: {experiment -> {model: file}}
    exp_maps: Dict[str, Dict[str, Path]] = {}
    for exp in experiments:
        exp_dir = DATA_DIR / exp
        exp_maps[exp] = _list_models(exp_dir, exp)
        print(f"📁 {exp}: {len(exp_maps[exp])} combined files")

    for experiment in experiments:
        model_files = exp_maps.get(experiment, {})
        if not model_files:
            print(f"\n📁 {experiment}: no files.")
            continue

        print(f"\n📁 Experiment: {experiment}  (models: {len(model_files)})")
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            if not start or not end:
                continue
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for model, scen_file in sorted(model_files.items()):
                try:
                    with _open_single(scen_file) as ds:
                        if varname not in ds:
                            continue

                        ds = _select_bbox(ds, lat_bounds, lon_bounds)
                        ds = _slice_time_cf(ds, start, end)
                        if ds.sizes.get("time", 0) == 0:
                            continue

                        tx = convert_units_to(ds[varname], "degC")

                        # daily boolean exceedance
                        above = (tx >= thr_c).chunk({"time": -1})

                        # count days per year, then mean across years (days/year)
                        per_year_counts = []
                        for yr, grp in above.groupby("time.year"):
                            grp = grp.chunk({"time": -1})
                            n_valid = grp.notnull().sum(dim="time")
                            n_above = grp.sum(dim="time")
                            days = n_above.where(n_valid > 0)
                            per_year_counts.append(days.assign_coords(year=yr))

                        if not per_year_counts:
                            continue

                        days_yearly = xr.concat(per_year_counts, dim="year")
                        days_mean = days_yearly.mean(dim="year").astype("float32").load()

                        if ens_sum is None:
                            ens_sum = days_mean.copy(); ens_n = 1
                        else:
                            if set(days_mean.dims) != set(ens_sum.dims) or any(
                                days_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in days_mean.dims
                            ):
                                days_mean = days_mean.reindex_like(ens_sum)
                            ens_sum = ens_sum + days_mean; ens_n += 1

                        used_models.append(model)

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TXge30 outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"txge30_ensemble_mean_{experiment}_{label}.nc"

            xr.Dataset(
                {"txge30": ensemble_mean.assign_attrs({
                    "units": "days",
                    "long_name": f"Mean days/year with TX ≥ {thr_c} °C",
                })},
                attrs={
                    "title": f"TXge30 Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "threshold_degC": thr_c,
                    "created_by": "txge30_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")