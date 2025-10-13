#!/usr/bin/env python3
"""
tnx_compute.py
---------------------------------------------------------------
TNx: Warmest daily minimum temperature (°C).
For each model & time slice:
  1) Convert tasmin to °C
  2) For each year: TNx_year = max(tasmin)  (annual max of daily Tmin)
  3) Slice mean = mean(TNx_year)  -> one value per grid cell
Multi-model ensemble mean is then saved per experiment & slice.

Outputs:
  data/outputs/tnx/tnx_ensemble_mean_<experiment>_<slice>.nc
Optional anomalies (Δ°C vs baseline):
  data/outputs/tnx/tnx_anomaly_<experiment>_<slice>.nc
"""

from __future__ import annotations
import os, re, glob, time, warnings
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

warnings.filterwarnings("ignore")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

# ---------------- helpers ----------------
def _label_slug(s: str) -> str:
    return (s.lower()
              .replace(" ", "_")
              .replace("(", "").replace(")", "")
              .replace("–", "-").replace("/", "-"))

def _engine_open_kwargs():
    # use_cftime keeps cftime calendars intact; h5netcdf tends to be robust
    return dict(engine="h5netcdf", use_cftime=True, lock=False)

def _normalize_latlon(ds: xr.Dataset) -> xr.Dataset:
    ren = {}
    if "latitude" in ds.dims and "lat" not in ds.dims:
        ren["latitude"] = "lat"
    if "longitude" in ds.dims and "lon" not in ds.dims:
        ren["longitude"] = "lon"
    return ds.rename(ren) if ren else ds

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    ds = _normalize_latlon(ds)
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _safe_time_slice(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    """Slice using same calendar type as ds['time'] (works for cftime & numpy)."""
    if "time" not in ds or ds.sizes.get("time", 0) == 0:
        return ds
    t0 = ds["time"].values[0]
    if isinstance(t0, np.datetime64):
        s = np.datetime64(start); e = np.datetime64(end)
    else:
        # build with the same cftime class as t0 if possible
        cls = type(t0)
        y0,m0,d0 = int(start[:4]), int(start[5:7]), int(start[8:10])
        y1,m1,d1 = int(end[:4]),   int(end[5:7]),   int(end[8:10])
        try:
            s = cls(y0,m0,d0); e = cls(y1,m1,d1)
        except TypeError:
            import cftime
            s = cftime.DatetimeProlepticGregorian(y0,m0,d0)
            e = cftime.DatetimeProlepticGregorian(y1,m1,d1)
    s = max(s, ds["time"].values[0]); e = min(e, ds["time"].values[-1])
    if e < s:
        return ds.isel(time=slice(0, 0))
    return ds.sel(time=slice(s, e))

_year_re = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
def _year_from_name(p: Path) -> Optional[int]:
    m = _year_re.search(p.name)
    return int(m.group()) if m else None

def _parse_model_from_filename(p: Path, varname: str, experiment: str) -> Optional[str]:
    """
    Supports combined naming: <var>_<MODEL>_<experiment>.nc
    and generic CMIP-like: var_day_<MODEL>_<experiment>_r...nc
    """
    stem = p.stem
    # combined pattern
    comb = f"{varname}_"
    if stem.startswith(comb) and stem.endswith(f"_{experiment}"):
        return stem[len(comb):-(len(experiment)+1)]
    # try CMIPish stem with underscores
    toks = stem.split("_")
    # e.g., tasmin_day_MPI-ESM1-2-LR_ssp585_r1i1p1f1_gn_2099
    for i, tok in enumerate(toks):
        if tok in {"historical", "ssp126", "ssp245", "ssp370", "ssp585"} and i >= 1:
            return toks[i-1]
    return None

def _scan_models(data_dir: Path, varname: str, experiment: str) -> List[str]:
    """Find unique models present under both layouts."""
    models = set()
    # combined layout
    for p in (data_dir / experiment).glob("*.nc"):
        m = _parse_model_from_filename(p, varname, experiment)
        if m: models.add(m)
    # uncombined layout
    for p in data_dir.glob(f"*/{experiment}/*.nc"):
        m = _parse_model_from_filename(p, varname, experiment)
        if m: models.add(m)
    return sorted(models)

def _files_for_model_exp(data_dir: Path, varname: str, model: str, experiment: str) -> List[Path]:
    """
    Return files for exactly one model+experiment, without mixing calendars.
    Prefers the combined file if present, else returns all per-year files.
    """
    # combined file?
    combined = data_dir / experiment / f"{varname}_{model}_{experiment}.nc"
    if combined.exists():
        return [combined.resolve()]

    # fallback: per-year files in var/<MODEL>/<experiment>/*.nc
    hits = sorted((data_dir / model / experiment).glob("*.nc"))
    # (small guard) ensure these are indeed that model
    hits = [p for p in hits if _parse_model_from_filename(p, varname, experiment) == model]
    return [p.resolve() for p in hits]

# -------------- per-file TNx ----------------
def _per_file_tnx_mean(
    fpath: Path, varname: str, lat_bounds, lon_bounds, start: str, end: str
) -> Optional[xr.DataArray]:
    """
    Open one file, subset to bbox & time, compute annual TNx, then mean over years.
    Returns 2D field [lat, lon] (float32) or None if empty.
    """
    ds = xr.open_dataset(str(fpath), chunks={"time": -1}, **_engine_open_kwargs())
    try:
        ds = _select_bbox(ds, lat_bounds, lon_bounds)
        ds = _safe_time_slice(ds, start, end)
        if varname not in ds or ds.sizes.get("time", 0) == 0:
            return None

        tmin = convert_units_to(ds[varname], "degC")

        per_year = []
        # groupby respects cftime calendars
        for yr, grp in tmin.groupby("time.year"):
            if grp.sizes.get("time", 0) == 0:
                continue
            # annual max of daily Tmin
            tnx_year = grp.max(dim="time")
            per_year.append(tnx_year.assign_coords(year=int(yr)))

        if not per_year:
            return None

        tnx_mean = xr.concat(per_year, dim="year").mean(dim="year").astype("float32")
        # load to prevent dangling dask graphs across many files
        return tnx_mean.load()
    finally:
        ds.close()

# -------------- main ----------------
def run(cfg):
    t0 = time.time()
    print("🧮 Starting TNx (warmest daily minimum, °C) — calendar-safe")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    icfg       = cfg.get("tnx", {})
    varname    = icfg.get("variable", "tasmin")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined" / varname   # works for combined; also scans uncombined
    OUTPUT_DIR = ROOT / "data" / "outputs" / "tnx"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    baseline_key = "Baseline (1995–2014)"
    baseline_label = _label_slug(baseline_key)
    baseline_ens = None

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")

        # discover models present for this experiment
        models = _scan_models(DATA_DIR, varname, experiment)
        print(f"   Models detected: {len(models)}")
        if not models:
            print("   ⚠️ No files found.")
            continue

        # which slices to run for this experiment
        slice_names = [baseline_key] if experiment == "historical" \
                      else [n for n in time_slices if n != baseline_key]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models = []

            for model in models:
                try:
                    files = _files_for_model_exp(DATA_DIR, varname, model, experiment)
                    if not files:
                        continue

                    # If many files (uncombined), process each file separately, then average across years
                    per_file_results = []
                    for f in files:
                        r = _per_file_tnx_mean(f, varname, lat_bounds, lon_bounds, start, end)
                        if r is not None:
                            per_file_results.append(r)

                    if not per_file_results:
                        continue

                    if len(per_file_results) == 1:
                        tnx_mean = per_file_results[0]
                    else:
                        # average the per-year means derived from each file
                        tnx_mean = xr.concat(per_file_results, dim="__file__").mean(dim="__file__").astype("float32")

                    if ens_sum is None:
                        ens_sum = tnx_mean.copy(); ens_n = 1
                    else:
                        tnx_mean = tnx_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + tnx_mean; ens_n += 1

                    used_models.append(model)

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TNx outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)

            # Save absolute
            out_abs = OUTPUT_DIR / f"tnx_ensemble_mean_{experiment}_{label}.nc"
            xr.Dataset(
                {"tnx": ensemble_mean.assign_attrs({"units": "degC", "long_name": "Warmest daily minimum temperature"})},
                attrs={
                    "title": f"TNx Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "created_by": "tnx_compute.py",
                },
            ).to_netcdf(out_abs, engine="h5netcdf")
            print(f"   ✅ Saved → {out_abs.name}  (models: {len(used_models)})")

            # cache baseline ensemble for anomaly step
            if experiment == "historical" and slice_name == baseline_key:
                baseline_ens = ensemble_mean

    # ---- Optional anomaly pass (Δ°C relative to baseline ensemble) ----
    if baseline_ens is not None:
        for experiment in [e for e in experiments if e != "historical"]:
            for slice_name, (start, end) in time_slices.items():
                if slice_name == baseline_key: 
                    continue
                label = _label_slug(slice_name)
                abs_path = OUTPUT_DIR / f"tnx_ensemble_mean_{experiment}_{label}.nc"
                if not abs_path.exists():
                    print(f"⚠️ Skipping anomaly for {experiment}/{label} (no absolute file).")
                    continue
                ds = xr.open_dataset(abs_path, **_engine_open_kwargs())
                try:
                    if "tnx" not in ds:
                        print(f"⚠️ 'tnx' not in {abs_path.name}; skipping anomaly.")
                        continue
                    tnx_abs = ds["tnx"]

                    base = baseline_ens
                    if (tnx_abs.sizes.get("lat") != base.sizes.get("lat")) or (tnx_abs.sizes.get("lon") != base.sizes.get("lon")):
                        base = base.reindex(lat=tnx_abs["lat"], lon=tnx_abs["lon"], method=None)

                    anom = (tnx_abs - base).astype("float32")
                    out_an = OUTPUT_DIR / f"tnx_anomaly_{experiment}_{label}.nc"
                    xr.Dataset({"tnx": anom.assign_attrs({"units": "degC", "long_name": "TNx anomaly vs 1995–2014"})}) \
                      .to_netcdf(out_an, engine="h5netcdf")
                    print(f"   ✅ Saved → {out_an.name}  (Δ°C vs baseline)")
                finally:
                    ds.close()

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")