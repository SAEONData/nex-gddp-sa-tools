#!/usr/bin/env python3
"""
spi_compute.py
---------------------------------------------------------------
Compute SPI3/SPI6/SPI12 as z-scores of rolling precipitation totals
relative to a historical baseline (default 1995–2014), and save
multi-model ensemble means by experiment & time slice.

This version avoids open_mfdataset (segfault risk) by:
- opening each file individually,
- converting to monthly totals per-file,
- concatenating monthly series for each model.

Units: unitless (standardized)
"""

from pathlib import Path
import glob, time, warnings
from collections import defaultdict, OrderedDict

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
    if "lat" not in ds.coords or "lon" not in ds.coords:
        raise ValueError("Dataset is missing 'lat' and/or 'lon'.")
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1])
    lon_asc = bool(lon[0] < lon[-1])
    lat_slice = slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0])
    lon_slice = slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0])
    return ds.sel(lat=lat_slice, lon=lon_slice)

def _group_files_by_model(files, experiment):
    groups = {}
    for f in files:
        p = Path(f)
        parts = p.parts
        if experiment in parts:
            idx = parts.index(experiment)
            model = parts[idx - 1]
        else:
            model = p.stem.split("_")[2]
        groups.setdefault(model, []).append(str(p))
    # sort models & each model's files by name (which sorts by year for NEX-GDDP)
    return OrderedDict((m, sorted(fs)) for m, fs in sorted(groups.items()))

def _file_to_monthly(pr_file, lat_bounds, lon_bounds):
    """Open 1 file, subset, convert to mm/day, resample to monthly totals (lazy)."""
    ds = xr.open_dataset(
        pr_file,
        engine="h5netcdf",          # usually more stable than netcdf4
        chunks={"time": -1},
        decode_times=True,
        mask_and_scale=True,
    )
    ds = _select_bbox(ds, lat_bounds, lon_bounds)
    if "pr" not in ds:
        raise ValueError("Missing 'pr' variable.")
    pr_day = convert_units_to(ds["pr"], "mm/day")
    pr_mon = pr_day.resample(time="MS").sum(dim="time")
    pr_mon = pr_mon.rename("pr_monthly_mm")
    pr_mon.attrs["units"] = "mm/month"
    return pr_mon

def _model_monthly_series(model_files, lat_bounds, lon_bounds):
    """Map-reduce: monthly totals per file, then concat along time."""
    monthly_list = []
    for f in model_files:
        try:
            pr_mon = _file_to_monthly(f, lat_bounds, lon_bounds)
            monthly_list.append(pr_mon)
        except Exception as e:
            print(f"   ⚠️ Skipped {Path(f).name}: {e}")
            continue
    if not monthly_list:
        return None
    series = xr.concat(monthly_list, dim="time").sortby("time")
    return series

def _rolling_total(pr_mon: xr.DataArray, k: int) -> xr.DataArray:
    return pr_mon.rolling(time=k, min_periods=k).sum().rename(f"P{k}")

def _baseline_stats(Pk: xr.DataArray, ref_start: str, ref_end: str):
    ref = Pk.sel(time=slice(ref_start, ref_end))
    mu = ref.mean(dim="time")
    sig = ref.std(dim="time")
    return mu, sig

def _spi_from_stats(Pk: xr.DataArray, mu: xr.DataArray, sig: xr.DataArray):
    eps = 1e-12
    sig_safe = xr.where(sig < eps, np.nan, sig)
    return (Pk - mu) / sig_safe

# ----------------- main -----------------
def run(cfg):
    t0 = time.time()
    print("🧮 Starting SPI (z-score) processing… (segfault-safe path)")

    # config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    spi_cfg   = cfg.get("spi", {})
    scales    = list(spi_cfg.get("scales", [3, 6, 12]))  # months
    ref_start = spi_cfg.get("reference_start", "1995-01-01")
    ref_end   = spi_cfg.get("reference_end",   "2014-12-31")

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "spi"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- Step A: build baseline stats per model (historical) ----------
    hist_files = sorted(glob.glob(str(DATA_DIR / "**/historical/*.nc"), recursive=True))
    if not hist_files:
        print("❌ No historical files found; SPI cannot be calibrated.")
        return
    groups_hist = _group_files_by_model(hist_files, "historical")

    stats = {}  # model -> {k: (mu, sig)}
    print("\n📊 Computing baseline stats (1995–2014) per model…")
    for model, files in groups_hist.items():
        series = _model_monthly_series(files, lat_bounds, lon_bounds)
        if series is None or series.time.size == 0:
            print(f"   ⚠️ {model}: no monthly data; skipping.")
            continue
        stats[model] = {}
        for k in scales:
            Pk = _rolling_total(series, k)
            mu, sig = _baseline_stats(Pk, ref_start, ref_end)
            stats[model][k] = (mu, sig)
        print(f"   ✓ {model}: baseline μ/σ")

    if not stats:
        print("❌ No baseline stats computed; aborting.")
        return

    # ---------- Step B: compute SPI per experiment/slice, ensemble over models ----------
    model_file_counts = defaultdict(int)

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        exp_files = sorted(glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        if not exp_files:
            print("   ⚠️ No files found.")
            continue
        groups_exp = _group_files_by_model(exp_files, experiment)

        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name, (start, end) in time_slices.items():
            if (experiment != "historical") and slice_name.startswith("Baseline"):
                continue
            if (experiment == "historical") and (slice_name != "Baseline (1995–2014)"):
                continue

            print(f"\n→ Time slice: {slice_name}  ({start} to {end})")

            per_scale_model_arrays = {k: [] for k in scales}
            per_scale_model_names  = {k: [] for k in scales}

            for model, files in groups_exp.items():
                if model not in stats:
                    continue
                series = _model_monthly_series(files, lat_bounds, lon_bounds)
                if series is None or series.time.size == 0:
                    continue

                for k in scales:
                    mu, sig = stats[model][k]
                    Pk = _rolling_total(series, k)
                    # slice AFTER rolling exists
                    Pk = Pk.sel(time=slice(start, end))
                    if Pk.time.size == 0:
                        continue
                    spi = _spi_from_stats(Pk, mu, sig)
                    spi_mean = spi.mean(dim="time")
                    per_scale_model_arrays[k].append(spi_mean)
                    per_scale_model_names[k].append(model)
                    model_file_counts[model] += 1

            # ensemble + save per scale
            for k in scales:
                if not per_scale_model_arrays[k]:
                    print(f"   ❌ No valid SPI{k} outputs for {experiment} / {slice_name}")
                    continue

                print(f"   → Computing ensemble mean (SPI{k})…")
                with ProgressBar():
                    computed = dask.compute(*per_scale_model_arrays[k])
                stack = xr.concat(computed, dim="model").assign_coords(model=("model", per_scale_model_names[k]))
                ensemble_mean = stack.mean(dim="model")

                out_var = f"spi{k}"
                out_da = ensemble_mean.assign_attrs({
                    "units": "",  # unitless
                    "long_name": f"Standardized Precipitation Index (SPI-{k}) — z-score",
                    "reference_period": f"{ref_start} to {ref_end}",
                    "scale_months": k,
                    "method": "zscore",
                })

                label = _label_slug(slice_name)
                out_nc = OUTPUT_DIR / f"spi{k}_ensemble_mean_{experiment}_{label}.nc"
                xr.Dataset({out_var: out_da}).to_netcdf(out_nc)
                print(f"   ✅ Saved → {out_nc.name}")

    # summary
    print("\n📊 File count per model (all experiments combined):")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")