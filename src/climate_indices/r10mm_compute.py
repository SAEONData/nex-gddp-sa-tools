#!/usr/bin/env python3
"""
r10mm_compute.py
---------------------------------------------------------------
Calculate and save CMIP6 R10mm: number of days with ≥10 mm rain.
Supports multiple experiments and time-slice-based aggregation.
"""

from pathlib import Path
import glob
import time
import numpy as np
import xarray as xr
from xclim.indices import wetdays
from dask.diagnostics import ProgressBar
import dask
import warnings

warnings.filterwarnings("ignore", message=".*already exists and will be overwritten.")

def run(cfg):
    start_time = time.time()
    print("Starting R10mm processing...")

    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    threshold = cfg.get("r10mm", {}).get("threshold_mm", 10.0)
    aggr      = cfg.get("r10mm", {}).get("aggregation", "annual")
    aggr_map  = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    aggr_code = aggr_map.get(aggr, "YS")

    time_slices = cfg.get("time_slices", {})

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "r10mm"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    from collections import defaultdict
    model_file_counts = defaultdict(lambda: defaultdict(int))

    for experiment in experiments:
        print(f"\n📁 Processing scenario: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f" Found {len(nc_files)} NetCDF files for '{experiment}'.\n")

        # Determine slice names
        if experiment == "historical":
            slice_names = ["Baseline (1995–2014)"]
        else:
            slice_names = [name for name in time_slices if not name.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f" → Time slice: {slice_name} ({start} to {end})")

            model_data, model_names = [], []

            for i, nc_file in enumerate(nc_files, 1):
                print(f"   [{i}/{len(nc_files)}] Processing: {nc_file.name}")
                try:
                    ds = xr.open_dataset(nc_file, chunks={"time": -1})
                    ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))
                    ds = ds.sel(time=slice(start, end))

                    if "pr" not in ds:
                        raise ValueError("Missing 'pr' variable in dataset.")

                    pr = ds["pr"] * 86400.0  # Convert kg/m²/s to mm/day
                    pr.attrs.update({
                        "units": "mm/day",
                        "cell_methods": "time: mean",
                        "standard_name": "precipitation_flux"
                    })

                    r10_result = wetdays(pr=pr, thresh=f"{threshold} mm/day", freq=aggr_code).compute()

                    if r10_result.isnull().all():
                        raise ValueError("All R10mm values are NaN.")

                    r10_mean = r10_result.mean(dim="time")

                    try:
                        idx = nc_file.parts.index(experiment)
                        model_name = nc_file.parts[idx - 1]
                    except ValueError:
                        model_name = nc_file.stem.split("_")[2]

                    model_file_counts[model_name][experiment] += 1
                    model_data.append(r10_mean)
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
            out_nc = OUTPUT_DIR / f"r10mm_ensemble_mean_{experiment}_{label}.nc"
            out_nc.parent.mkdir(parents=True, exist_ok=True)

            unique_models = sorted(set(model_names))
            ds_out = xr.Dataset(
                {"r10mm": ensemble_mean},
                attrs={
                    "title": f"Ensemble Mean of R10mm (Days ≥ {threshold} mm Rain) - {experiment} - {slice_name}",
                    "description": f"Aggregation: {aggr}, Threshold: {threshold} mm/day, Slice: {slice_name}",
                    "units": "days",
                    "models_included": ", ".join(unique_models),
                    "created_by": "R10mm processing script",
                }
            )

            ds_out.to_netcdf(out_nc)
            print(f"   ✅ Saved NetCDF → {out_nc}")

    print("\n📊 File summary per model/scenario:")
    for model, exp_data in sorted(model_file_counts.items()):
        row = f"{model:30}"
        for exp in experiments:
            count = exp_data.get(exp, 0)
            row += f" {exp}: {count:2d}"
        print(row)

    print(f"\n⏱️ Completed in {round(time.time() - start_time, 1)} seconds.")