#!/usr/bin/env python3
"""
cdd_compute.py
---------------------------------------------------------------
Calculate and plot CMIP6 maximum consecutive dry days (CDD)
aggregated by South African vegetation biomes, with configurable
thresholds and aggregation (annual, monthly, seasonal).
"""

from pathlib import Path
import glob, time
import numpy as np
import xarray as xr
from xclim.core.indicator import registry
from dask.diagnostics import ProgressBar
import dask
import warnings
warnings.filterwarnings("ignore", message="Class CDD already exists and will be overwritten.")

def run(cfg):
    start_time = time.time()
    print("Starting CDD processing...\n")
    
    # ───── Config ─────
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    threshold  = cfg["cdd"].get("threshold_mm", 1.0)
    aggr       = cfg["cdd"].get("aggregation", "annual")
    
    aggr_code = cfg["cdd"].get("aggregation_code", "YS")

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "cdd"

    experiments = cfg.get("experiments", {}).get("select", ["historical"])
    CDD = registry.get("CDD")
    if CDD is None:
        raise RuntimeError("'CDD' indicator not registered in xclim.")

    for experiment in experiments:
        print(f"\n Running experiment: {experiment}")

        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")

        if not nc_files:
            print(f"⚠️  No files found for {experiment}. Skipping.\n")
            continue

        # ───── Count files per model ─────
        from collections import defaultdict
        model_file_counts = defaultdict(int)

        for f in nc_files:
            try:
                idx = f.parts.index(experiment)
                model = f.parts[idx - 1]
            except ValueError:
                model = f.stem.split("_")[2]
            model_file_counts[model] += 1

        expected_files_per_model = 1
        print(f"{'Model':30} {'Files Found':>12} {'Status'}")
        for model, count in sorted(model_file_counts.items()):
            status = "✅" if count >= expected_files_per_model else "❌ MISSING"
            print(f"{model:30} {count:12} {status}")
        print("\n")

        model_data, model_names = [], []

        for i, nc_file in enumerate(nc_files, 1):
            print(f" [{i}/{len(nc_files)}] Processing: {nc_file.name}")
            try:
                ds = xr.open_dataset(nc_file, chunks={"time": -1})
                ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))

                if "pr" not in ds:
                    raise ValueError("Missing 'pr' variable in dataset.")
                    
                pr = ds["pr"] * 86400.0  # Convert to mm/day
                pr.attrs.update({
                    "units": "mm/day",
                    "cell_methods": "time: mean",
                    "standard_name": "precipitation_flux"
                })

                cdd_instance = CDD()
                cdd_result = cdd_instance(pr=pr, thresh=f"{threshold} mm/day", freq=aggr_code)
                cdd_result = cdd_result.compute()

                if cdd_result.isnull().all():
                    raise ValueError("All CDD values are NaN.")

                cdd_mean = cdd_result.mean(dim="time")

                try:
                    idx = nc_file.parts.index(experiment)
                    model_name = nc_file.parts[idx - 1]
                except ValueError:
                    model_name = nc_file.stem.split("_")[2]

                model_data.append(cdd_mean)
                model_names.append(model_name)

            except Exception as e:
                print(f"⚠️ Error processing {nc_file.name}: {e}")

        if not model_data:
            print(f"❌ No valid outputs for {experiment}. Skipping.")
            continue

        # ───── Compute Ensemble Mean ─────
        print(f"\nComputing ensemble mean for {experiment}...")
        with ProgressBar():
            computed_data = dask.compute(*model_data)
            stack = xr.concat(computed_data, dim="model")
            stack["model"] = model_names
            ensemble_mean = stack.mean(dim="model")

        # ───── Save Output ─────
        out_nc = OUTPUT_DIR / f"cdd_ensemble_mean_{experiment}.nc"
        out_nc.parent.mkdir(parents=True, exist_ok=True)

        unique_models = sorted(set(model_names))
        ds_out = xr.Dataset(
            {"max_cdd": ensemble_mean},
            attrs={
                "title": f"Ensemble Mean of Max Consecutive Dry Days (CDD) - {experiment}",
                "description": f"Threshold: {threshold} mm/day, Aggregation: {aggr}",
                "units": "days",
                "models_included": ", ".join(unique_models),
                "created_by": "CDD processing script",
            }
        )

        ds_out.to_netcdf(out_nc)
        print(f"✅ Saved NetCDF → {out_nc}")

    print(f"\n All experiments completed in {round(time.time() - start_time, 1)} seconds.")