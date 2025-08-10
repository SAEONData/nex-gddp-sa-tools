#!/usr/bin/env python3
"""
tnx_compute.py
---------------------------------------------------------------
TNx: Warmest daily minimum temperature (°C).
For each model & time slice:
  1) Convert tasmin to °C
  2) For each year: TNx_year = max(tasmin)  (annual max of daily Tmin)
  3) Slice mean = mean(TNx_year)  -> one value per grid cell (days removed)
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
os.environ.setdefault("XR_USE_FLOX", "0")

def _label_slug(s: str) -> str:
    return (s.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("–", "-").replace("/", "-"))

def _engine_open_kwargs():
    return dict(engine="h5netcdf", use_cftime=True, lock=False)

def _is_healthy_file(p: Path) -> bool:
    try:
        xr.open_dataset(str(p), chunks={}, **_engine_open_kwargs()).close()
        return True
    except Exception:
        return False

def _open_mf(paths):
    paths = [p for p in paths if _is_healthy_file(p)]
    if not paths:
        raise IOError("No healthy files remain after screening.")
    return xr.open_mfdataset(
        [str(p) for p in paths],
        combine="by_coords",
        parallel=True,
        chunks={"time": -1},
        **_engine_open_kwargs(),
    )

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    if "time" not in ds or ds.sizes.get("time", 0) == 0:
        return ds
    t0 = ds["time"].values[0]
    if isinstance(t0, np.datetime64):
        s = np.datetime64(start); e = np.datetime64(end)
    else:
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
    if e < s: return ds.isel(time=slice(0, 0))
    return ds.sel(time=slice(s, e))

_year_re = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
def _year_from_name(p: Path):
    m = _year_re.search(p.name)
    return int(m.group()) if m else None

def _files_for_model_exp(data_dir: Path, model: str, experiment: str):
    hits = sorted(data_dir.glob(f"**/{model}/{experiment}/*.nc"))
    if hits: return hits
    return sorted(p for p in data_dir.glob(f"**/{experiment}/*.nc") if model in str(p))

def _find_model_name(p: Path, experiment: str) -> str:
    try:
        idx = p.parts.index(experiment)
        if idx > 0: return p.parts[idx - 1]
    except ValueError:
        pass
    toks = p.stem.split("_")
    return toks[2] if len(toks) > 2 else "unknown_model"

def _models_in_files(files, experiment: str):
    return sorted({_find_model_name(p, experiment) for p in files})

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TNx (warmest daily minimum, °C) — streaming ensemble…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    icfg       = cfg.get("tnx", {})
    varname    = icfg.get("variable", "tasmin")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "tnx"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory
    exp_files: Dict[str, List[Path]] = {
        exp: sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{exp}/*.nc"), recursive=True))
        for exp in experiments
    }

    # Optional: cache baseline ensemble for anomalies
    baseline_key = "Baseline (1995–2014)"
    baseline_label = _label_slug(baseline_key)
    baseline_ens = None

    for experiment, files in exp_files.items():
        print(f"\n📁 Experiment: {experiment}  ({len(files)} files)")
        if not files: continue

        slice_names = [baseline_key] if experiment == "historical" else \
                      [n for n in time_slices if n != baseline_key]

        models = _models_in_files(files, experiment)
        print(f"   Models detected: {len(models)}")

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            sY, eY = int(start[:4]), int(end[:4])
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models = []

            for model in models:
                try:
                    scen_files = _files_for_model_exp(DATA_DIR, model, experiment)
                    scen_files = [p for p in scen_files if (y := _year_from_name(p)) and (sY <= y <= eY)] or scen_files
                    if not scen_files: continue

                    ds = _open_mf(scen_files)
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = _slice_time_cf(ds, start, end)
                    if varname not in ds or ds.sizes.get("time", 0) == 0:
                        ds.close(); continue

                    tmin = convert_units_to(ds[varname], "degC")

                    # Annual max of daily Tmin, then mean across years in the slice
                    per_year_tnx = []
                    for yr, grp in tmin.groupby("time.year"):
                        grp = grp.chunk({"time": -1})
                        tnx_year = grp.max(dim="time")
                        per_year_tnx.append(tnx_year.assign_coords(year=yr))

                    if not per_year_tnx:
                        ds.close(); continue

                    tnx_mean = xr.concat(per_year_tnx, dim="year").mean(dim="year").astype("float32").load()

                    if ens_sum is None:
                        ens_sum = tnx_mean.copy(); ens_n = 1
                    else:
                        tnx_mean = tnx_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + tnx_mean; ens_n += 1

                    used_models.append(model)
                    ds.close()

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

            # Keep baseline ensemble in memory for anomaly step
            if experiment == "historical" and slice_name == baseline_key:
                baseline_ens = ensemble_mean

    # ---- Optional anomaly pass (Δ°C relative to baseline ensemble) ----
    if baseline_ens is not None:
        for experiment in [e for e in experiments if e != "historical"]:
            for slice_name, (start, end) in time_slices.items():
                if slice_name == baseline_key: continue
                label = _label_slug(slice_name)
                abs_path = OUTPUT_DIR / f"tnx_ensemble_mean_{experiment}_{label}.nc"
                if not abs_path.exists():
                    print(f"⚠️ Skipping anomaly for {experiment}/{label} (no absolute file).")
                    continue
                ds = xr.open_dataset(abs_path, **_engine_open_kwargs())
                if "tnx" not in ds: 
                    print(f"⚠️ 'tnx' not in {abs_path.name}; skipping anomaly."); ds.close(); continue

                # Align grids if needed
                tnx_abs = ds["tnx"]
                base = baseline_ens
                if (tnx_abs.sizes.get("lat") != base.sizes.get("lat")) or (tnx_abs.sizes.get("lon") != base.sizes.get("lon")):
                    base = base.reindex(lat=tnx_abs["lat"], lon=tnx_abs["lon"], method=None)

                anom = (tnx_abs - base).astype("float32")
                out_an = OUTPUT_DIR / f"tnx_anomaly_{experiment}_{label}.nc"
                xr.Dataset({"tnx": anom.assign_attrs({"units": "degC", "long_name": "TNx anomaly vs 1995–2014"})}).to_netcdf(out_an, engine="h5netcdf")
                ds.close()
                print(f"   ✅ Saved → {out_an.name}  (Δ°C vs baseline)")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")