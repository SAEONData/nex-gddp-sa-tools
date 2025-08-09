#!/usr/bin/env python3
"""
tnn_compute.py
---------------------------------------------------------------
Calculate and save ensemble mean of Coldest Daily Minimum Temperature (TNn)
for CMIP6 tasmin data across time slices and scenarios.
"""

from pathlib import Path
import glob
import time
import numpy as np
import xarray as xr
import xclim
from xclim.indices import tn_min
from dask.diagnostics import ProgressBar
import warnings
import dask  # ✅ Needed for computing lazy dask arrays
warnings.filterwarnings("ignore")

def run(cfg):
    start_time = time.time()
    print("Starting TNn processing...\n")

    # ───── Config ─────
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "tasmin"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "tnn"

    for experiment in experiments:
        print(f"\n ▶ Running experiment: {experiment}")

        # Define applicable time slices
        if experiment == "historical":
            slice_names = ["Baseline (1995–2014)"]
        else:
            slice_names = [name for name in time_slices if not name.startswith("Baseline")]

        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")

        if not nc_files:
            print(f"⚠️ No files found for {experiment}. Skipping.")
            continue

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

                    if "tasmin" not in ds:
                        raise ValueError("Missing 'tasmin' variable in dataset.")

                    tasmin = ds["tasmin"] - 273.15  # Convert to Celsius
                    tasmin.attrs.update({
                        "units": "degC",
                        "cell_methods": "time: min",
                        "standard_name": "air_temperature"
                    })

                    tnn = tn_min(tasmin, freq="YS").mean(dim="time")

                    if tnn.isnull().all():
                        raise ValueError("All TNn values are NaN.")

                    model_name = nc_file.parts[-3] if experiment in nc_file.parts else nc_file.stem.split("_")[2]
                    model_data.append(tnn)
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
            out_nc = OUTPUT_DIR / f"tnn_ensemble_mean_{experiment}_{label}.nc"
            out_nc.parent.mkdir(parents=True, exist_ok=True)

            ds_out = xr.Dataset(
                {"tnn": ensemble_mean},
                attrs={
                    "title": f"Ensemble Mean of TNn - {experiment} - {slice_name}",
                    "description": "Coldest Daily Minimum Temperature (TNn) from tasmin",
                    "units": "degC",
                    "models_included": ", ".join(sorted(set(model_names))),
                    "created_by": "tnn_compute.py",
                }
            )

            ds_out.to_netcdf(out_nc)
            print(f"✅ Saved NetCDF → {out_nc}")

    print(f"\n✅ All TNn processing complete in {round(time.time() - start_time, 1)} seconds.")