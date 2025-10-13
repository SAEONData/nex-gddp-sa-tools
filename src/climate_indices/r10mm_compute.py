#!/usr/bin/env python3
"""
r10mm_compute.py
---------------------------------------------------------------
Compute CMIP6 R10mm (number of days with PR ≥ 10 mm) and save
ensemble means by experiment and time slice.

- Keeps computations lazy until the ensemble step
- Robust to descending lat/lon coordinates
- Correct units on the data variable so anomaly step inherits them
"""

from pathlib import Path
import glob, time, warnings
from collections import defaultdict

import dask
from dask.diagnostics import ProgressBar
import xarray as xr
from xclim.indices import wetdays
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
        raise ValueError("Dataset is missing 'lat' and/or 'lon' coordinates.")
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1])
    lon_asc = bool(lon[0] < lon[-1])
    lat_slice = slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0])
    lon_slice = slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0])
    return ds.sel(lat=lat_slice, lon=lon_slice)

def run(cfg):
    t0 = time.time()
    print("🧮 Starting R10mm processing...")

    # ── config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    r10_cfg   = cfg.get("r10mm", {})
    threshold = float(r10_cfg.get("threshold_mm", 10.0))
    aggr      = r10_cfg.get("aggregation", "annual")
    aggr_map  = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    aggr_code = aggr_map.get(aggr, "YS")

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    # paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined"/ "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "r10mm"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # inventory
    model_file_counts = defaultdict(int)

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")
        if not nc_files:
            print("   ⚠️ No files. Skipping.")
            continue

        # applicable slices
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f"\n→ Time slice: {slice_name}  ({start} to {end})")

            model_data, model_names = [], []

            for i, nc_file in enumerate(nc_files, 1):
                print(f"   [{i}/{len(nc_files)}] {nc_file.name}")
                try:
                    ds = xr.open_dataset(nc_file, chunks={"time": -1})
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    if start or end:
                        ds = ds.sel(time=slice(start, end))

                    if "pr" not in ds:
                        raise ValueError("Missing 'pr' variable.")

                    # convert to mm/day (avoid manual *86400)
                    pr = convert_units_to(ds["pr"], "mm/day").assign_attrs({
                        "units": "mm/day",
                        "cell_methods": "time: mean",
                        "standard_name": "precipitation_flux",
                    })

                    # count wet days ≥ threshold, aggregated by freq (keep lazy)
                    r10 = wetdays(pr=pr, thresh=f"{threshold} mm/day", freq=aggr_code)

                    if r10.size == 0:
                        raise ValueError("Empty result after resampling.")

                    # average across aggregated periods in this slice (keep lazy)
                    r10_mean = r10.mean(dim="time")

                    # model name
                    model_name = (
                        ds.attrs.get("source_id") or ds.attrs.get("model_id") or
                        (nc_file.parts[nc_file.parts.index(experiment) - 1] if experiment in nc_file.parts else nc_file.stem.split("_")[2])
                    )

                    model_data.append(r10_mean)
                    model_names.append(model_name)
                    model_file_counts[model_name] += 1

                except Exception as e:
                    print(f"   ⚠️ Skipped {nc_file.name}: {e}")

            if not model_data:
                print(f"   ❌ No valid outputs for {experiment} / {slice_name}. Skipping slice.")
                continue

            # ensemble
            print(f"   → Computing ensemble mean…")
            with ProgressBar():
                computed = dask.compute(*model_data)
            stack = xr.concat(computed, dim="model").assign_coords(model=("model", model_names))
            ensemble_mean = stack.mean(dim="model")

            # variable-level attrs (days)
            r10mm_da = ensemble_mean.assign_attrs({
                "units": "days",
                "long_name": f"Number of days with PR ≥ {threshold} mm",
                "threshold": f"{threshold} mm/day",
                "aggregation": aggr,
            })

            # save
            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"r10mm_ensemble_mean_{experiment}_{label}.nc"
            ds_out = xr.Dataset(
                {"r10mm": r10mm_da},
                attrs={
                    "title": f"Ensemble Mean of R10mm - {experiment} - {slice_name}",
                    "description": f"Days with PR ≥ {threshold} mm; aggregation: {aggr} ({aggr_code})",
                    "models_included": ", ".join(sorted(set(model_names))),
                    "created_by": "r10mm_compute.py",
                },
            )
            ds_out.to_netcdf(out_nc)
            print(f"   ✅ Saved NetCDF → {out_nc.name}")

    # summary
    print("\n📊 File count per model (all experiments combined):")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")