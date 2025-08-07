#!/usr/bin/env python3
"""
cwd_compute.py
---------------------------------------------------------------
Calculate and plot CMIP6 maximum consecutive wet days (CWD)
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
warnings.filterwarnings("ignore", message="Class CWD already exists and will be overwritten.")

def run(cfg):
    start_time = time.time()
    print("Starting CWD processing...\n")

    # ───── Config ─────
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    threshold  = cfg["cwd"].get("threshold_mm", 1.0)
    aggr       = cfg["cwd"].get("aggregation", "annual")
    aggr_code  = cfg["cwd"].get("aggregation_code", "YS")
    time_slices = cfg.get("time_slices", {})

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "cwd"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    CWD = registry.get("CWD")
    if CWD is None:
        raise RuntimeError("'CWD' indicator not registered in xclim.")

    for experiment in experiments:
        print(f"\n📁 Processing experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f" Found {len(nc_files)} NetCDF files.\n")

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

                    pr = ds["pr"] * 86400.0
                    pr.attrs.update({
                        "units": "mm/day",
                        "cell_methods": "time: mean",
                        "standard_name": "precipitation_flux"
                    })

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

                    model_data.append(cwd_mean)
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
            out_nc = OUTPUT_DIR / f"cwd_ensemble_mean_{experiment}_{label}.nc"
            out_nc.parent.mkdir(parents=True, exist_ok=True)

            unique_models = sorted(set(model_names))
            ds_out = xr.Dataset(
                {"max_cwd": ensemble_mean},
                attrs={
                    "title": f"Ensemble Mean of Max CWD - {experiment} - {slice_name}",
                    "description": f"Threshold: {threshold} mm/day, Aggregation: {aggr}, Slice: {slice_name}",
                    "units": "days",
                    "models_included": ", ".join(unique_models),
                    "created_by": "CWD processing script",
                }
            )

            ds_out.to_netcdf(out_nc)
            print(f"   ✅ Saved NetCDF → {out_nc}")

    print(f"\n⏱️ Completed in {round(time.time() - start_time, 1)} seconds.")