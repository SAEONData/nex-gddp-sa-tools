#!/usr/bin/env python3
"""
txx_compute.py
---------------------------------------------------------------
Compute TXx (warmest daily maximum temperature, °C) for each
experiment and configured time slice, then save the multi-model
ensemble mean.

Outputs:
  data/outputs/txx/txx_ensemble_mean_<experiment>_<slice>.nc
"""

from pathlib import Path
import glob, time, warnings
from collections import defaultdict

import dask
from dask.diagnostics import ProgressBar
import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

warnings.filterwarnings("ignore", message=".*already exists and will be overwritten.")

# ----------------- helpers -----------------
def _label_slug(s: str) -> str:
    return (
        s.lower()
         .replace(" ", "_")
         .replace("(", "").replace(")", "")
         .replace("–", "-")
    )

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    if "lat" not in ds.coords or "lon" not in ds.coords:
        raise ValueError("Dataset is missing 'lat' and/or 'lon'.")
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1])
    lon_asc = bool(lon[0] < lon[-1])
    lat_slice = slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0])
    lon_slice = slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0])
    return ds.sel(lat=lat_slice, lon=lon_slice)

# ----------------- main -----------------
def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXx (warmest daily Tmax) processing…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    txx_cfg = cfg.get("txx", {})
    varname  = txx_cfg.get("variable", "tasmax")
    aggr     = txx_cfg.get("aggregation", "annual")  # 'annual'|'seasonal'|'monthly'
    aggr_map = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    freq_code = aggr_map.get(aggr, "YS")

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined"/ varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txx"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    model_file_counts = defaultdict(int)

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")
        if not nc_files:
            continue

        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f"\n→ Time slice: {slice_name}  ({start} to {end})")

            model_data, model_names = [], []

            for i, nc_file in enumerate(nc_files, 1):
                print(f"   [{i}/{len(nc_files)}] {nc_file.name}")
                try:
                    ds = xr.open_dataset(nc_file, chunks={"time": -1})
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = ds.sel(time=slice(start, end))

                    if varname not in ds:
                        print(f"     ⚠️ Missing variable '{varname}'; skipping.")
                        continue

                    # Convert to °C
                    tasmax_c = convert_units_to(ds[varname], "degC")

                    # Per-period maximum of daily Tmax (TXx)
                    txx_per = tasmax_c.resample(time=freq_code).max(dim="time")
                    if txx_per.size == 0 or txx_per.isnull().all():
                        print("     ⚠️ Empty/NaN TXx; skipping.")
                        continue

                    # Mean across periods within the slice
                    txx_mean = txx_per.mean(dim="time")

                    # Model name
                    try:
                        idx = nc_file.parts.index(experiment)
                        model = nc_file.parts[idx - 1]
                    except ValueError:
                        model = ds.attrs.get("source_id") or ds.attrs.get("model_id") or nc_file.stem.split("_")[2]

                    model_data.append(txx_mean)
                    model_names.append(model)
                    model_file_counts[model] += 1

                except Exception as e:
                    print(f"     ⚠️ Error processing {nc_file.name}: {e}")

            if not model_data:
                print(f"   ❌ No valid outputs for {experiment} / {slice_name}.")
                continue

            print("   → Computing ensemble mean…")
            with ProgressBar():
                computed = dask.compute(*model_data)
            stack = xr.concat(computed, dim="model").assign_coords(model=("model", model_names))
            ensemble_mean = stack.mean(dim="model")

            out_da = ensemble_mean.assign_attrs({
                "units": "°C",
                "long_name": "TXx (Annual/period Maximum of Daily Tmax)",
                "aggregation": aggr,
                "aggregation_code": freq_code,
                "models_included": ", ".join(sorted(set(model_names))),
                "created_by": "txx_compute.py",
            })

            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"txx_ensemble_mean_{experiment}_{label}.nc"
            xr.Dataset({"txx": out_da}).to_netcdf(out_nc)
            print(f"   ✅ Saved → {out_nc.name}")

    print("\n📊 Files per model (any slice):")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")