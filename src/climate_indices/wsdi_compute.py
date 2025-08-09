#!/usr/bin/env python3
"""
wsdi_compute.py
---------------------------------------------------------------
Compute WSDI (Warm Spell Duration Index) for CMIP6 experiments
using a cached TX90p threshold from the historical baseline.
"""

import os
import time
import glob
import warnings
from pathlib import Path
import numpy as np
import xarray as xr
import dask
from dask.diagnostics import ProgressBar

warnings.filterwarnings("ignore")
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

# ──────────────── Helper Functions ────────────────
def compute_tx90p(tasmax, ref_start, ref_end):
    base = tasmax.sel(time=slice(ref_start, ref_end))
    if base.time.size == 0:
        raise ValueError("⚠️ Reference period is empty.")
    return base.groupby("time.dayofyear").reduce(np.percentile, q=90, dim="time")

def detect_warm_spells(tasmax, tx90p, min_length):
    doy = tasmax["time"].dt.dayofyear
    threshold = tx90p.sel(dayofyear=doy)
    warm = tasmax > threshold
    warm_np = warm.values
    spell_mask = np.zeros_like(warm_np)

    for lat in range(warm.shape[1]):
        for lon in range(warm.shape[2]):
            series = warm_np[:, lat, lon]
            run = 0
            for t in range(len(series)):
                if series[t]:
                    run += 1
                else:
                    if run >= min_length:
                        spell_mask[t - run:t, lat, lon] = 1
                    run = 0
            if run >= min_length:
                spell_mask[len(series) - run:, lat, lon] = 1

    return xr.DataArray(spell_mask, coords=tasmax.coords, dims=tasmax.dims)

# ──────────────── Main ────────────────
def run(cfg):
    start_time = time.time()
    print("🚀 Starting WSDI processing...\n")

    # ─── Config ───
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    rcfg = cfg["wsdi"]
    min_spell_length = rcfg.get("min_spell_length", 6)
    ref_start = rcfg.get("reference_start", "1995-01-01")
    ref_end = rcfg.get("reference_end", "2014-12-31")

    time_slices = cfg["time_slices"]
    ROOT = Path(__file__).resolve().parents[2]
    DATA_DIR = ROOT / "data" / "tasmax"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "wsdi"
    threshold_path = OUTPUT_DIR / "wsdi_threshold_tx90p.nc"

    experiments = cfg.get("experiments", {}).get("select", [])
    all_nc_files = {exp: sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{exp}/*.nc"), recursive=True)) for exp in experiments}

    # ─── STEP 1: Compute and save TX90p threshold ───
    if not threshold_path.exists():
        print("🧮 Computing TX90p threshold from historical data...")
        historical_files = all_nc_files.get("historical", [])
        threshold_data = []

        for f in historical_files:
            try:
                ds = xr.open_dataset(f, chunks={"time": -1})
                ds = ds.convert_calendar("standard", use_cftime=True)
                ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))
                tasmax = ds["tasmax"] - 273.15  # Kelvin to °C
                tasmax.attrs["units"] = "degC"
                tx90p = compute_tx90p(tasmax, ref_start, ref_end)
                threshold_data.append(tx90p)
            except Exception as e:
                print(f"⚠️ Skipping {f.name}: {e}")

        if not threshold_data:
            print("❌ No valid TX90p thresholds computed. Exiting.")
            return

        with ProgressBar():
            computed = dask.compute(*threshold_data)
            tx90p_stack = xr.concat(computed, dim="model")
            tx90p_final = tx90p_stack.mean(dim="model")

        tx90p_final.name = "tx90p"
        tx90p_final.to_netcdf(threshold_path)
        print(f"✅ Saved TX90p threshold → {threshold_path}")
    else:
        print("✅ TX90p threshold already exists.")
        tx90p_final = xr.open_dataarray(threshold_path)

    # ─── STEP 2: Process all experiments (including historical if needed) ───
    for experiment in experiments:
        print(f"\n▶ Processing experiment: {experiment}")
        nc_files = all_nc_files[experiment]

        if not nc_files:
            print(f"⚠️ No NetCDF files for {experiment}. Skipping.")
            continue

        for slice_name, (start, end) in time_slices.items():
            if experiment != "historical" and slice_name.startswith("Baseline"):
                continue

            print(f"\n → Time slice: {slice_name} ({start} to {end})")

            model_data, model_names = [], []

            for i, f in enumerate(nc_files, 1):
                print(f"   [{i}/{len(nc_files)}] {f.name}")
                try:
                    ds = xr.open_dataset(f, chunks={"time": -1})
                    ds = ds.convert_calendar("standard", use_cftime=True)
                    ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))
                    tasmax = ds["tasmax"].sel(time=slice(start, end)) - 273.15
                    tasmax.attrs["units"] = "degC"

                    mask = detect_warm_spells(tasmax, tx90p_final, min_length=min_spell_length)
                    annual_wsdi = mask.groupby("time.year").sum(dim="time")
                    wsdi_mean = annual_wsdi.mean(dim="year")

                    if wsdi_mean.isnull().all():
                        raise ValueError("All values are NaN.")

                    model_name = f.parts[f.parts.index(experiment) - 1]
                    model_data.append(wsdi_mean)
                    model_names.append(model_name)

                except Exception as e:
                    print(f"⚠️ Error processing {f.name}: {e}")

            if not model_data:
                print("❌ No valid models for this slice.")
                continue

            print("📊 Computing ensemble mean...")
            with ProgressBar():
                computed = dask.compute(*model_data)
                stack = xr.concat(computed, dim="model")
                stack["model"] = model_names
                ensemble_mean = stack.mean(dim="model")
                ensemble_mean = ensemble_mean.where(ensemble_mean != 0)  # ✅ Mask out ocean/zero padding

            label = slice_name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("–", "-")
            out_nc = OUTPUT_DIR / f"wsdi_ensemble_mean_{experiment}_{label}.nc"
            out_nc.parent.mkdir(parents=True, exist_ok=True)

            ds_out = xr.Dataset(
                {"wsdi": ensemble_mean},
                attrs={
                    "title": f"Ensemble Mean of WSDI - {experiment} - {slice_name}",
                    "description": (
                        f"Warm Spell Duration Index: TX > 90th percentile for ≥ {min_spell_length} days.\n"
                        f"Reference: {ref_start} to {ref_end}"
                    ),
                    "units": "days",
                    "created_by": "wsdi_compute.py",
                    "models_included": ", ".join(sorted(set(model_names)))
                }
            )

            ds_out.to_netcdf(out_nc)
            print(f"   ✅ Saved → {out_nc}")

    print(f"\n✅ All WSDI computations completed in {round(time.time() - start_time, 1)} seconds.")