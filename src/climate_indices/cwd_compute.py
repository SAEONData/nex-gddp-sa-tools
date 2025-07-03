#!/usr/bin/env python3
"""
cwd_compute.py
---------------------------------------------------------------
Calculate and plot CMIP6 maximum consecutive dry days (CDD)
aggregated by South African vegetation biomes, with configurable
thresholds and aggregation (annual, monthly, seasonal).
"""

from pathlib import Path
import glob, time
import numpy as np
import pandas as pd
import xarray as xr
import geopandas as gpd
import regionmask
import matplotlib.pyplot as plt
from xclim.core.indicator import registry
from dask.diagnostics import ProgressBar
import dask
import warnings
warnings.filterwarnings("ignore", message="Class CWD already exists and will be overwritten.")

def run(cfg):
    import os
    start_time = time.time()
    print("Starting CWD processing...")

    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    threshold  = cfg["cwd"].get("threshold_mm", 1.0)
    aggr       = cfg["cwd"].get("aggregation", "annual")

    aggr_code = cfg["cdd"].get("aggregation_code", "YS")

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "cwd"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    from collections import defaultdict
    model_file_counts = defaultdict(lambda: defaultdict(int))

    CWD = registry.get("CWD")
    if CWD is None:
        raise RuntimeError("'CWD' indicator not registered in xclim.")

    for experiment in experiments:
        print(f"\n📁 Processing scenario: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))

        print(f" Found {len(nc_files)} NetCDF files for '{experiment}'.\n")

        model_data, model_names = [], []

        for i, nc_file in enumerate(nc_files, 1):
            print(f" [{i}/{len(nc_files)}] Processing: {nc_file.name}")
            try:
                ds = xr.open_dataset(nc_file, chunks={"time": -1})
                ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))

                if "pr" not in ds:
                    raise ValueError("Missing 'pr' variable in dataset.")

                pr = ds["pr"] * 86400.0
                pr.attrs["units"] = "mm/day"
                pr.attrs["cell_methods"] = "time: mean"
                pr.attrs["standard_name"] = "precipitation_flux"

                cwd_instance = CWD()
                cwd_result = cwd_instance(pr=pr, thresh=f"{threshold} mm/day", freq=aggr_code)
                cwd_result = cwd_result.compute()

                if cwd_result.isnull().all():
                    raise ValueError("All CWD values are NaN.")

                cwd_mean = cwd_result.mean(dim="time")

                try:
                    idx = nc_file.parts.index(experiment)
                    model_name = nc_file.parts[idx - 1]
                except ValueError:
                    model_name = nc_file.stem.split("_")[2]

                model_file_counts[model_name][experiment] += 1
                model_data.append(cwd_mean)
                model_names.append(model_name)

            except Exception as e:
                print(f"Error processing {nc_file.name}: {e}\n")

        if not model_data:
            print(f"⚠️ No successful files for {experiment}. Skipping ensemble.")
            continue

        # ───── Ensemble Mean ─────
        print(" Computing ensemble mean...")
        with ProgressBar():
            computed_data = dask.compute(*model_data)
            stack = xr.concat(computed_data, dim="model")
            stack["model"] = model_names
            ensemble_mean = stack.mean(dim="model")

        out_nc = OUTPUT_DIR / f"cwd_ensemble_mean_{experiment}.nc"
        unique_models = sorted(set(model_names))
        ds_out = xr.Dataset(
            {"max_cwd": ensemble_mean},
            attrs={
                "title": f"Ensemble Mean of Max Consecutive Wet Days (CWD) - {experiment}",
                "description": f"Threshold: {threshold} mm/day, Aggregation: {aggr}",
                "units": "days",
                "models_included": ", ".join(unique_models),
                "created_by": "CWD processing script",
            }
        )

        ds_out.to_netcdf(out_nc)
        print(f"✅ Saved ensemble NetCDF → {out_nc}")

    print("\n📊 File summary per model/scenario:")
    for model, exp_data in sorted(model_file_counts.items()):
        row = f"{model:30}"
        for exp in experiments:
            count = exp_data.get(exp, 0)
            row += f" {exp}: {count:2d}"
        print(row)

    print(f"\n⏱️ Completed in {round(time.time() - start_time, 1)} seconds.")