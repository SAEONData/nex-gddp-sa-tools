#!/usr/bin/env python3
"""
sdii_compute.py
---------------------------------------------------------------
Compute SDII (Simple Daily Intensity Index): mean precipitation
intensity on wet days (PR >= threshold), per grid cell.

Definition (ETCCDI):
  SDII = (sum of daily precipitation on wet days) / (number of wet days)
Units: mm/day

Implementation notes
- Works for historical + scenarios, by time slices from config.
- Converts PR to mm/day.
- Aggregates by period using a freq code (annual default 'YS'; supports 'MS', 'QS-DEC').
- For each slice: average SDII across periods in the slice, then ensemble-mean across models.
- Output: data/outputs/sdii/sdii_ensemble_mean_<experiment>_<slice>.nc
"""

from pathlib import Path
import glob, time, warnings
from collections import defaultdict

import dask
from dask.diagnostics import ProgressBar
import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

warnings.filterwarnings("ignore", message=".*already exists and will be overwritten.")

# ----------------- helpers -----------------
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

def _sdii_with_xclim(pr_mm_day: xr.DataArray, thresh_mm: float, freq_code: str) -> xr.DataArray:
    """Try xclim.indices.sdii; raise to let caller fallback if not available."""
    from xclim.indices import sdii as xclim_sdii  # may not exist on some versions
    return xclim_sdii(pr_mm_day, thresh=f"{thresh_mm} mm/day", freq=freq_code)

def _sdii_manual(pr_mm_day: xr.DataArray, thresh_mm: float, freq_code: str) -> xr.DataArray:
    """Manual ETCCDI-equivalent: per-period sum(pr where pr>=thresh)/count(pr>=thresh)."""
    wet = pr_mm_day >= thresh_mm
    pr_wet = pr_mm_day.where(wet)

    # Per-period sums and counts
    sum_wet = pr_wet.resample(time=freq_code).sum(dim="time")
    cnt_wet = wet.resample(time=freq_code).sum(dim="time")  # True->1

    # Avoid divide-by-zero (no wet days in a period)
    with np.errstate(invalid="ignore", divide="ignore"):
        sdii = sum_wet / cnt_wet
    sdii = sdii.where(np.isfinite(sdii))
    sdii.attrs.update({"units": "mm/day", "long_name": "Simple Daily Intensity Index"})
    return sdii

# ----------------- main -----------------
def run(cfg):
    t0 = time.time()
    print("🧮 Starting SDII processing...")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    sdii_cfg = cfg.get("sdii", {})
    wetday_threshold = float(sdii_cfg.get("threshold_mm", 1.0))
    aggr = sdii_cfg.get("aggregation", "annual")  # 'annual'|'seasonal'|'monthly'
    aggr_map = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    freq_code = aggr_map.get(aggr, "YS")

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "sdii"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory report
    model_file_counts = defaultdict(int)

    # Try to use xclim; if missing, fall back
    have_xclim_sdii = True
    try:
        from xclim.indices import sdii as _tmp  # noqa
    except Exception:
        have_xclim_sdii = False
        print("ℹ️  xclim.indices.sdii not found; using manual SDII computation.")

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        print(f"   Found {len(nc_files)} NetCDF files.")
        if not nc_files:
            continue

        # Time slices to run
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f"\n→ Time slice: {slice_name} ({start} to {end})")

            model_data, model_names = [], []

            for i, nc_file in enumerate(nc_files, 1):
                print(f"   [{i}/{len(nc_files)}] {nc_file.name}")
                try:
                    ds = xr.open_dataset(nc_file, chunks={"time": -1})
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = ds.sel(time=slice(start, end))

                    if "pr" not in ds:
                        print(f"     ⚠️ Missing 'pr'; skipping.")
                        continue

                    # Convert to mm/day
                    pr = convert_units_to(ds["pr"], "mm/day")
                    pr.attrs.update({
                        "units": "mm/day",
                        "standard_name": "precipitation_flux",
                        "cell_methods": "time: mean",
                    })

                    # SDII per aggregation period
                    if have_xclim_sdii:
                        try:
                            sdii_per = _sdii_with_xclim(pr, wetday_threshold, freq_code)
                        except Exception as _:
                            # fall back if xclim signature/version mismatch
                            sdii_per = _sdii_manual(pr, wetday_threshold, freq_code)
                    else:
                        sdii_per = _sdii_manual(pr, wetday_threshold, freq_code)

                    if sdii_per.size == 0 or sdii_per.isnull().all():
                        print("     ⚠️ Empty/NaN SDII; skipping.")
                        continue

                    # Average across periods in the slice (e.g., mean of annual SDII)
                    sdii_mean = sdii_per.mean(dim="time")

                    # Model name
                    try:
                        idx = nc_file.parts.index(experiment)
                        model_name = nc_file.parts[idx - 1]
                    except ValueError:
                        model_name = ds.attrs.get("source_id") or ds.attrs.get("model_id") or nc_file.stem.split("_")[2]

                    model_data.append(sdii_mean)
                    model_names.append(model_name)
                    model_file_counts[model_name] += 1

                except Exception as e:
                    print(f"     ⚠️ Error processing {nc_file.name}: {e}")

            if not model_data:
                print(f"   ❌ No valid outputs for {experiment} / {slice_name}.")
                continue

            # Ensemble mean across models
            print("   → Computing ensemble mean…")
            with ProgressBar():
                computed = dask.compute(*model_data)
            stack = xr.concat(computed, dim="model").assign_coords(model=("model", model_names))
            ensemble_mean = stack.mean(dim="model")

            out_da = ensemble_mean.assign_attrs({
                "units": "mm/day",
                "long_name": "Simple Daily Intensity Index (SDII)",
                "wetday_threshold": f"{wetday_threshold} mm/day",
                "aggregation": aggr,
                "aggregation_code": freq_code,
                "models_included": ", ".join(sorted(set(model_names))),
                "created_by": "sdii_compute.py",
            })

            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"sdii_ensemble_mean_{experiment}_{label}.nc"
            xr.Dataset({"sdii": out_da}).to_netcdf(out_nc)
            print(f"   ✅ Saved → {out_nc.name}")

    # Summary table
    print("\n📊 File count per model (any slice):")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")