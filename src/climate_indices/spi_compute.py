#!/usr/bin/env python3
"""
spi_compute.py
---------------------------------------------------------------
Calculate and save CMIP6 Standardised Precipitation Index (SPI)
at 3-, 6-, and 12-month scales using xclim's SPI indicator.

This version first computes ensemble mean precipitation, then
calculates SPI – which is the correct statistical order.
"""

from pathlib import Path
import glob
import time
import warnings

import numpy as np
import xarray as xr
import dask
from dask.diagnostics import ProgressBar
from xclim.indicators.atmos import standardized_precipitation_index
from xclim.core.units import convert_units_to

warnings.filterwarnings("ignore")


def run(cfg):
    start_time = time.time()
    print("📈 Starting SPI processing...")

    # ─── Config ───
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    spicfg = cfg.get("spi", {})

    ROOT = Path(__file__).resolve().parents[2]
    DATA_DIR = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "spi"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    experiments = cfg.get("experiments", {}).get("select", ["historical"])
    dist = spicfg.get("distribution", "gamma")
    method = spicfg.get("method", "ML")
    cal_start = spicfg.get("reference_start", "1981-01-01")
    cal_end = spicfg.get("reference_end", "2010-12-31")
    scales = spicfg.get("scales", [3, 6, 12])
    freq = "MS"  # Monthly resampling

    for experiment in experiments:
        print(f"\n▶︎ Experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} files.")

        if not nc_files:
            print("⚠ No files found. Skipping.")
            continue

        pr_list = []
        model_names = []

        for i, f in enumerate(nc_files, 1):
            print(f" [{i}/{len(nc_files)}] Reading: {f.name}")
            try:
                ds = xr.open_dataset(f, chunks={"time": -1})
                ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))

                if "pr" not in ds or ds.time.size < 3:
                    raise ValueError("Invalid dataset (missing 'pr' or insufficient time points)")

                pr = convert_units_to(ds["pr"], "mm/day")

                pr_list.append(pr)
                try:
                    idx = f.parts.index(experiment)
                    model = f.parts[idx - 1]
                except ValueError:
                    model = f.stem.split("_")[2]
                model_names.append(model)

            except Exception as e:
                print(f" ⚠ Error loading {f.name}: {e}")

        if not pr_list:
            print("❌ No valid precipitation datasets for ensemble mean.")
            continue

        # ─── Compute ensemble mean precipitation ───
        print("📊 Computing ensemble mean precipitation...")
        with ProgressBar():
            pr_ens = xr.concat(dask.compute(*pr_list), dim="model")
            pr_ens["model"] = model_names
            pr_mean = pr_ens.mean("model")
            pr_mean = pr_mean.sortby("time")  # Ensure chronological order

        # 🔄 Convert cftime calendar to standard datetime64 if needed
        if not np.issubdtype(pr_mean.time.dtype, np.datetime64):
            print("🕒 Converting time to standard Gregorian calendar...")
            time_values = [np.datetime64(str(t)) for t in pr_mean.time.values]
            pr_mean = xr.Dataset({"pr": pr_mean}).assign_coords(time=time_values)["pr"]

        # ─── Compute SPI for each timescale ───
        for scale in scales:
            print(f"📈 Calculating SPI-{scale}...")
            try:
                spi = standardized_precipitation_index(
                    pr=pr_mean,
                    window=scale,
                    freq=freq,
                    dist=dist,
                    method=method,
                    cal_start=cal_start,
                    cal_end=cal_end
                ).compute()

                out_path = OUTPUT_DIR / f"spi{scale}_ensemble_{experiment}.nc"
                spi_ds = xr.Dataset(
                    {f"spi{scale}": spi},
                    attrs={
                        "title": f"SPI-{scale} Ensemble – {experiment}",
                        "description": f"SPI on ensemble precipitation at {scale}-month scale (dist={dist}, method={method}, ref={cal_start} to {cal_end})",
                        "units": "unitless",
                        "models_included": ", ".join(sorted(set(model_names))),
                        "created_by": "spi_compute.py"
                    }
                )
                spi_ds.to_netcdf(out_path)
                print(f"✅ Saved → {out_path}")

            except Exception as e:
                print(f" ⚠ Failed SPI-{scale}: {e}")

    print(f"\n✔ Done in {round(time.time() - start_time, 1)} seconds.")