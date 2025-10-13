#!/usr/bin/env python3
"""
rx1day_compute.py
---------------------------------------------------------------
Compute Rx1day (wettest day of the period) per grid cell from
daily precipitation, for each experiment and time slice, then
save the multi-model ensemble mean.

Definition (ETCCDI):
  Rx1day = maximum 1-day precipitation amount in the period.

Outputs:
  data/outputs/rx1day/rx1day_ensemble_mean_<experiment>_<slice>.nc
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
    """Subset robustly regardless of ascending/descending coords."""
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
    print("🧮 Starting Rx1day processing... (manual computation, no xclim.indices.rx1day)")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    rx_cfg   = cfg.get("rx1day", {})
    aggr     = rx_cfg.get("aggregation", "annual")  # 'annual' | 'seasonal' | 'monthly'
    aggr_map = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    aggr_code = aggr_map.get(aggr, "YS")

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined"/ "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "rx1day"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    model_file_counts = defaultdict(int)

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")
        if not nc_files:
            continue

        # Which slices to compute
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

                    if "pr" not in ds:
                        print("     ⚠️ Missing 'pr'; skipping.")
                        continue

                    # Convert to daily totals in mm/day
                    pr = convert_units_to(ds["pr"], "mm/day")
                    pr.attrs.update({"units": "mm/day", "standard_name": "precipitation_flux"})

                    # Per-period maximum of daily precipitation (Rx1day)
                    # e.g., yearly max when aggr_code='YS'
                    rx1_per = pr.resample(time=aggr_code).max(dim="time")

                    if rx1_per.size == 0 or rx1_per.isnull().all():
                        print("     ⚠️ Empty/NaN Rx1day; skipping.")
                        continue

                    # Average across periods within the slice
                    rx1_mean = rx1_per.mean(dim="time")

                    # Model name
                    try:
                        idx = nc_file.parts.index(experiment)
                        model = nc_file.parts[idx - 1]
                    except ValueError:
                        model = ds.attrs.get("source_id") or ds.attrs.get("model_id") or nc_file.stem.split("_")[2]

                    model_data.append(rx1_mean)
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
                "units": "mm",  # daily total amount on the wettest day
                "long_name": "Rx1day (Annual/period Maximum 1-day Precipitation)",
                "aggregation": aggr,
                "aggregation_code": aggr_code,
                "models_included": ", ".join(sorted(set(model_names))),
                "created_by": "rx1day_compute.py",
            })

            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"rx1day_ensemble_mean_{experiment}_{label}.nc"
            xr.Dataset({"rx1day": out_da}).to_netcdf(out_nc)
            print(f"   ✅ Saved → {out_nc.name}")

    print("\n📊 Files per model (any slice):")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")