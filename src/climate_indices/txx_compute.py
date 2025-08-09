#!/usr/bin/env python3
"""
txx_compute.py
---------------------------------------------------------------
Compute TXx (Annual Max of Daily Maximum Temperature) for CMIP6 experiments.
TXx is defined as the highest daily tasmax value per year.
"""

from pathlib import Path
import glob
import time
import warnings
import numpy as np
import xarray as xr
import dask
from dask.diagnostics import ProgressBar

warnings.filterwarnings("ignore")

def run(cfg):
    start_time = time.time()
    print("🚀 Starting TXx (Annual Max of Daily Tmax) processing...\n")

    # ─── Config ───
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    rcfg = cfg["txx"]
    aggr_label = rcfg.get("aggregation", "annual")
    aggr_code = rcfg.get("aggregation_code", "YS")

    time_slices = cfg.get("time_slices", {})
    ROOT = Path(__file__).resolve().parents[2]
    DATA_DIR = ROOT / "data" / "tasmax"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txx"

    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    for experiment in experiments:
        print(f"\n▶ Running experiment: {experiment}")

        if experiment == "historical":
            slice_names = ["Baseline (1995–2014)"]
        else:
            slice_names = [name for name in time_slices if not name.startswith("Baseline")]

        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")

        if not nc_files:
            print(f"⚠️  No files found for {experiment}. Skipping.\n")
            continue

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f" → Time slice: {slice_name} ({start} to {end})")

            model_data, model_names = [], []

            for i, nc_file in enumerate(nc_files, 1):
                print(f"   [{i}/{len(nc_files)}] Processing: {nc_file.name}")
                try:
                    ds = xr.open_dataset(nc_file, chunks={"time": -1})
                    ds = ds.convert_calendar("standard", use_cftime=True)
                    ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))
                    tasmax = ds["tasmax"].sel(time=slice(start, end)) - 273.15  # Convert to °C
                    tasmax.attrs["units"] = "degC"

                    # Manual TXx: max tasmax per year, then mean over years
                    annual_txx = tasmax.resample(time=aggr_code).max(dim="time")
                    txx_mean = annual_txx.mean(dim="time")

                    if txx_mean.isnull().all():
                        raise ValueError("All TXx values are NaN.")

                    idx = nc_file.parts.index(experiment)
                    model_name = nc_file.parts[idx - 1]
                    model_data.append(txx_mean)
                    model_names.append(model_name)

                except Exception as e:
                    print(f"⚠️ Error processing {nc_file.name}: {e}")

            if not model_data:
                print(f"❌ No valid outputs for {experiment} / {slice_name}. Skipping.")
                continue

            print(f"\n   → Computing ensemble mean for {experiment} / {slice_name}...")
            with ProgressBar():
                computed_data = dask.compute(*model_data)
                stack = xr.concat(computed_data, dim="model")
                stack["model"] = model_names
                ensemble_mean = stack.mean(dim="model")

            label = slice_name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("–", "-")
            out_nc = OUTPUT_DIR / f"txx_ensemble_mean_{experiment}_{label}.nc"
            out_nc.parent.mkdir(parents=True, exist_ok=True)

            ds_out = xr.Dataset(
                {"txx": ensemble_mean},
                attrs={
                    "title": f"Ensemble Mean of TXx - {experiment} - {slice_name}",
                    "description": f"Annual max of daily max temperature (tasmax). Aggregation: {aggr_label}, Slice: {slice_name}",
                    "units": "degC",
                    "models_included": ", ".join(sorted(set(model_names))),
                    "created_by": "txx_compute.py (manual txx via resample().max())",
                }
            )

            ds_out.to_netcdf(out_nc)
            print(f"   ✅ Saved NetCDF → {out_nc}")

    print(f"\n✅ All TXx computations completed in {round(time.time() - start_time, 1)} seconds.")