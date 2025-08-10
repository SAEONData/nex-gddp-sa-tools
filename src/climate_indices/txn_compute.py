#!/usr/bin/env python3
"""
txn_compute.py
---------------------------------------------------------------
TXn: Coldest daily maximum temperature (°C).

For each model and time slice:
  1) Compute the minimum of daily tasmax per year (annual TXn)
  2) Average those annual TXn values within the slice
  3) Ensemble-average across models

Inputs:
  data/tasmax/<MODEL>/<EXPERIMENT>/<tasmax>_<year>.nc
Outputs:
  data/outputs/txn/txn_ensemble_mean_<experiment>_<slice>.nc
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

try:
    import dask
    dask.config.set({"array.slicing.split_large_chunks": True})
except Exception:
    pass

# ----------------- helpers ----------------- #

def _label_slug(s: str) -> str:
    return (s.lower()
              .replace(" ", "_")
              .replace("(", "").replace(")", "")
              .replace("–", "-").replace("/", "-"))

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _engine_open_kwargs():
    return dict(engine="h5netcdf", use_cftime=True, lock=False)

def _is_healthy_file(p: Path) -> bool:
    try:
        xr.open_dataset(str(p), chunks={}, **_engine_open_kwargs()).close()
        return True
    except Exception:
        return False

def _open_mf(paths: List[Path]) -> xr.Dataset:
    paths = [p for p in paths if _is_healthy_file(p)]
    if not paths:
        raise IOError("No healthy files remain after screening.")
    try:
        return xr.open_mfdataset(
            [str(p) for p in paths],
            combine="by_coords",
            parallel=True,
            chunks={"time": -1},
            **_engine_open_kwargs(),
        )
    except Exception:
        return xr.open_mfdataset([str(p) for p in paths], combine="by_coords",
                                 parallel=True, chunks={"time": -1}, use_cftime=True, lock=False)

_year_re = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
def _year_from_name(p: Path) -> Optional[int]:
    m = _year_re.search(p.name)
    return int(m.group()) if m else None

def _files_for_model_exp(data_dir: Path, model: str, experiment: str) -> List[Path]:
    hits = sorted(data_dir.glob(f"**/{model}/{experiment}/*.nc"))
    if hits:
        return hits
    return sorted(p for p in data_dir.glob(f"**/{experiment}/*.nc") if model in str(p))

def _find_model_name(p: Path, experiment: str) -> str:
    try:
        idx = p.parts.index(experiment)
        if idx > 0:
            return p.parts[idx - 1]
    except ValueError:
        pass
    toks = p.stem.split("_")
    return toks[2] if len(toks) > 2 else "unknown_model"

def _models_in_files(files: List[Path], experiment: str) -> List[str]:
    return sorted({_find_model_name(p, experiment) for p in files})

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    if "time" not in ds or ds.sizes.get("time", 0) == 0:
        return ds
    t0 = ds["time"].values[0]
    if isinstance(t0, np.datetime64):
        s = np.datetime64(start); e = np.datetime64(end)
    else:
        y0,m0,d0 = int(start[:4]), int(start[5:7]), int(start[8:10])
        y1,m1,d1 = int(end[:4]),   int(end[5:7]),   int(end[8:10])
        try:
            s = type(t0)(y0,m0,d0); e = type(t0)(y1,m1,d1)
        except TypeError:
            import cftime
            s = cftime.DatetimeProlepticGregorian(y0,m0,d0)
            e = cftime.DatetimeProlepticGregorian(y1,m1,d1)
    tmin = ds["time"].values[0]; tmax = ds["time"].values[-1]
    s = s if s > tmin else tmin
    e = e if e < tmax else tmax
    if e < s:
        return ds.isel(time=slice(0, 0))
    return ds.sel(time=slice(s, e))

# ----------------- main ----------------- #

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXn (coldest daily maximum temperature) — streaming ensemble…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg      = cfg.get("txn", {})
    varname   = icfg.get("variable", "tasmax")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txn"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory
    exp_files: Dict[str, List[Path]] = {
        exp: sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{exp}/*.nc"), recursive=True))
        for exp in experiments
    }

    for experiment, files in exp_files.items():
        print(f"\n📁 Experiment: {experiment}  ({len(files)} files)")
        if not files:
            continue

        # For historical compute only the Baseline slice; for SSPs, all non-baseline
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.lower().startswith("baseline")]

        models = _models_in_files(files, experiment)
        print(f"   Models detected: {len(models)}")

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            if not start or not end:
                continue
            sY, eY = int(start[:4]), int(end[:4])
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for model in models:
                try:
                    scen_files = _files_for_model_exp(DATA_DIR, model, experiment)
                    scen_files = [p for p in scen_files if (y := _year_from_name(p)) and (sY <= y <= eY)] or scen_files
                    if not scen_files:
                        continue

                    ds = _open_mf(scen_files)
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = _slice_time_cf(ds, start, end)
                    if varname not in ds or ds.sizes.get("time", 0) == 0:
                        ds.close()
                        continue

                    tx = convert_units_to(ds[varname], "degC")

                    # Annual TXn: min of daily tasmax per-year, then mean across years
                    annual_txn = tx.resample(time="YS").min(dim="time")
                    txn_mean   = annual_txn.mean(dim="time").astype("float32").load()

                    if ens_sum is None:
                        ens_sum = txn_mean.copy()
                        ens_n = 1
                    else:
                        if set(txn_mean.dims) != set(ens_sum.dims) or any(
                            txn_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in txn_mean.dims
                        ):
                            txn_mean = txn_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + txn_mean
                        ens_n += 1

                    used_models.append(model)
                    ds.close()

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TXn outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"txn_ensemble_mean_{experiment}_{label}.nc"

            xr.Dataset(
                {"txn": ensemble_mean.assign_attrs({
                    "units": "degC",
                    "long_name": "Coldest daily maximum temperature (TXn)",
                    "definition": "Mean of annual minima of daily tasmax within the time slice",
                })},
                attrs={
                    "title": f"TXn Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "created_by": "txn_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")