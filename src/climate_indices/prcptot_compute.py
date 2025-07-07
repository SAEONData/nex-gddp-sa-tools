#!/usr/bin/env python3
"""
prcptot_compute.py
---------------------------------------------------------------
Calculate and save CMIP6 PRCPTOT: total rainfall on wet days (≥1 mm/day).
Supports multiple experiments and aggregation levels.
"""

from pathlib import Path
import glob
import time
import numpy as np
import xarray as xr
from xclim.indices import wetdays
from xclim.core.units import convert_units_to
from dask.diagnostics import ProgressBar
import dask
import warnings

warnings.filterwarnings("ignore", message=".*already exists and will be overwritten.")

def run(cfg):
    start_time = time.time()
    print("Starting PRCPTOT processing...")

    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    threshold = cfg.get("prcptot", {}).get("threshold_mm", 1.0)
    aggr      = cfg.get("prcptot", {}).get("aggregation", "annual")
    aggr_map  = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    aggr_code = aggr_map.get(aggr, "YS")

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "prcptot"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    from collections import defaultdict
    model_file_counts = defaultdict(lambda: defaultdict(int))

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

                # Convert to mm/day and update attributes
                pr = convert_units_to(ds["pr"], "mm/day")
                pr.attrs.update({
                    "units": "mm/day",
                    "standard_name": "precipitation_flux",
                    "long_name": "Precipitation",
                    "cell_methods": "time: mean"
                })

                # Mask out non-wet days (pr < threshold)
                pr_masked = pr.where(pr >= threshold)

                # Resample and sum only over wet days
                prcptot = pr_masked.resample(time=aggr_code).sum(dim="time").compute()

                if prcptot.isnull().all():
                    raise ValueError("All PRCPTOT values are NaN.")

                prcptot_mean = prcptot.mean(dim="time")

                try:
                    idx = nc_file.parts.index(experiment)
                    model_name = nc_file.parts[idx - 1]
                except ValueError:
                    model_name = nc_file.stem.split("_")[2]

                model_file_counts[model_name][experiment] += 1
                model_data.append(prcptot_mean)
                model_names.append(model_name)

            except Exception as e:
                print(f"Error processing {nc_file.name}: {e}\n")

        if not model_data:
            print(f"⚠️ No successful files for {experiment}. Skipping ensemble.")
            continue

        print(" Computing ensemble mean...")
        with ProgressBar():
            computed_data = dask.compute(*model_data)
            stack = xr.concat(computed_data, dim="model")
            stack["model"] = model_names
            ensemble_mean = stack.mean(dim="model")

        out_nc = OUTPUT_DIR / f"prcptot_ensemble_mean_{experiment}.nc"
        unique_models = sorted(set(model_names))
        ds_out = xr.Dataset(
            {"prcptot": ensemble_mean},
            attrs={
                "title": f"Ensemble Mean of PRCPTOT (Rainfall ≥ {threshold} mm/day) - {experiment}",
                "description": (
                    f"Total precipitation on wet days (≥ {threshold} mm/day). "
                    f"Aggregation: {aggr}, CMIP6 experiment: {experiment}"
                ),
                "units": "mm",
                "models_included": ", ".join(unique_models),
                "created_by": "PRCPTOT processing script",
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