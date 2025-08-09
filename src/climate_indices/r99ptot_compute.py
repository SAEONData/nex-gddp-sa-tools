#!/usr/bin/env python3
"""
r99ptot_compute.py
---------------------------------------------------------------
Compute R99pTOT: Percentage of total precipitation from "extremely wet days"
(> 99th percentile threshold from the baseline period, per grid cell).

- Baseline (historical) percentile is computed once per model and reused.
- Output is the multi-model ensemble mean per experiment and time slice.
- Units: %
"""

from pathlib import Path
import glob, time, warnings
from collections import defaultdict

import dask
from dask.diagnostics import ProgressBar
import xarray as xr
import numpy as np
from xclim.core.units import convert_units_to

warnings.filterwarnings("ignore", message=".*already exists and will be overwritten.")

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

def run(cfg):
    t0 = time.time()
    print("🧮 Starting R99pTOT processing...")

    # ── config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    r99_cfg   = cfg.get("r99ptot", {})
    wetday_threshold = float(r99_cfg.get("wetday_threshold_mm", 1.0))  # mm/day
    baseline_slice   = r99_cfg.get("baseline_slice", "Baseline (1995–2014)")
    percentile       = int(r99_cfg.get("percentile", 99))              # default 99

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    # paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "r99ptot"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    model_file_counts = defaultdict(int)
    percentile_cache = {}

    # ── Step 1: Compute baseline 99th percentile thresholds (per model)
    print(f"\n📊 Computing baseline {percentile}th percentile from: {baseline_slice}")
    baseline_start, baseline_end = time_slices.get(baseline_slice, [None, None])
    hist_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / "**/historical/*.nc"), recursive=True))

    for nc_file in hist_files:
        try:
            ds = xr.open_dataset(nc_file, chunks={"time": -1})
            ds = _select_bbox(ds, lat_bounds, lon_bounds)
            ds = ds.sel(time=slice(baseline_start, baseline_end))

            if "pr" not in ds:
                print(f"   ⚠️ Missing 'pr' in {nc_file.name}, skipping baseline.")
                continue

            pr = convert_units_to(ds["pr"], "mm/day")
            pr_wet = pr.where(pr >= wetday_threshold)

            pct = pr_wet.quantile(percentile / 100.0, dim="time", skipna=True)

            try:
                model_name = nc_file.parts[nc_file.parts.index("historical") - 1]
            except ValueError:
                model_name = ds.attrs.get("source_id") or ds.attrs.get("model_id") or nc_file.stem.split("_")[2]

            percentile_cache[model_name] = pct
            print(f"   ✓ Baseline threshold computed for {model_name}")

        except Exception as e:
            print(f"   ⚠️ Skipped baseline for {nc_file.name}: {e}")

    if not percentile_cache:
        print("❌ No baseline percentiles computed. Aborting R99pTOT.")
        return

    # ── Step 2: Apply baseline to all experiments/time slices, then ensemble
    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        if not nc_files:
            print("   ⚠️ No files found.")
            continue

        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f"\n→ Time slice: {slice_name}  ({start} to {end})")

            model_data, model_names = [], []

            for nc_file in nc_files:
                try:
                    ds = xr.open_dataset(nc_file, chunks={"time": -1})
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = ds.sel(time=slice(start, end))

                    if "pr" not in ds:
                        print(f"   ⚠️ Missing 'pr' in {nc_file.name}, skipping.")
                        continue

                    pr = convert_units_to(ds["pr"], "mm/day")
                    pr_wet = pr.where(pr >= wetday_threshold)

                    try:
                        model_name = nc_file.parts[nc_file.parts.index(experiment) - 1]
                    except ValueError:
                        model_name = ds.attrs.get("source_id") or ds.attrs.get("model_id") or nc_file.stem.split("_")[2]

                    if model_name not in percentile_cache:
                        print(f"   ⚠️ No baseline pct for {model_name}, skipping.")
                        continue

                    thr = percentile_cache[model_name]
                    very_wet = pr_wet > thr

                    prc_vwet  = pr_wet.where(very_wet).sum(dim="time")  # mm
                    prc_total = pr_wet.sum(dim="time")                  # mm

                    with np.errstate(invalid="ignore", divide="ignore"):
                        r99ptot = (prc_vwet / prc_total) * 100.0
                        r99ptot = r99ptot.where(np.isfinite(r99ptot))

                    model_data.append(r99ptot)
                    model_names.append(model_name)
                    model_file_counts[model_name] += 1

                except Exception as e:
                    print(f"   ⚠️ Skipped {nc_file.name}: {e}")

            if not model_data:
                print(f"   ❌ No valid outputs for {experiment} / {slice_name}")
                continue

            with ProgressBar():
                computed = dask.compute(*model_data)
            stack = xr.concat(computed, dim="model").assign_coords(model=("model", model_names))
            ensemble_mean = stack.mean(dim="model")

            out_da = ensemble_mean.assign_attrs({
                "units": "%",
                "long_name": f"R99pTOT: % of total precipitation from days > {percentile}th percentile (baseline)",
                "wetday_threshold": f"{wetday_threshold} mm/day",
                "percentile": percentile,
                "baseline_slice": baseline_slice,
            })

            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"r99ptot_ensemble_mean_{experiment}_{label}.nc"
            xr.Dataset({"r99ptot": out_da}).to_netcdf(out_nc)
            print(f"   ✅ Saved → {out_nc.name}")

    print("\n📊 File count per model:")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")