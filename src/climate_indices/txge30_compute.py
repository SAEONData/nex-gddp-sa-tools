#!/usr/bin/env python3
"""
txge30_compute.py
---------------------------------------------------------------
TXge30: Mean number of days per year with tasmax >= 30 °C
for each model and time slice, then ensemble-averaged.

Units: days (per year)
Inputs:
  data/tasmax/<MODEL>/<EXPERIMENT>/<tasmax>_<year>.nc
Outputs:
  data/outputs/txge30/txge30_ensemble_mean_<experiment>_<slice>.nc

Config (example keys expected in a YAML you load and pass to run(cfg)):
  region.lat_min, region.lat_max, region.lon_min, region.lon_max
  experiments.select: [historical, ssp126, ...]
  time_slices:
    Baseline (1995–2014): ["1995-01-01", "2014-12-31"]
    Mid-century (2041–2060): ["2041-01-01","2060-12-31"]
  txge30:
    variable: tasmax
    threshold_c: 30.0
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
os.environ.setdefault("XR_USE_FLOX", "0")  # safer reductions

try:
    import dask
    dask.config.set({"array.slicing.split_large_chunks": True})
except Exception:
    pass

# ----------------------- helpers ----------------------- #

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
    # fallback if model not in path positions
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
        cls = type(t0)
        y0,m0,d0 = int(start[:4]), int(start[5:7]), int(start[8:10])
        y1,m1,d1 = int(end[:4]),   int(end[5:7]),   int(end[8:10])
        try:
            s = cls(y0,m0,d0); e = cls(y1,m1,d1)
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

# ----------------------- main ----------------------- #

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXge30 (days/year with TX ≥ 30°C) — streaming ensemble…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg      = cfg.get("txge30", {})
    varname   = icfg.get("variable", "tasmax")
    thr_c     = float(icfg.get("threshold_c", 30.0))

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txge30"
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

        # For historical, you may want only the baseline slice; for SSPs, all non-baseline slices
        slice_names = list(time_slices.keys()) if experiment != "historical" else \
                      [n for n in time_slices if "1995" in time_slices[n][0] and "2014" in time_slices[n][1]] or list(time_slices.keys())

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
                    # keep only years overlapping the slice (based on filename year token)
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
                    # boolean exceedances
                    above = (tx >= thr_c)
                    above = above.chunk({"time": -1})

                    # count days per year, then average across years -> days/year
                    per_year_counts = []
                    for yr, grp in above.groupby("time.year"):
                        grp = grp.chunk({"time": -1})
                        n_valid = grp.notnull().sum(dim="time")
                        n_above = grp.sum(dim="time")
                        # If you prefer to require a minimum valid-day fraction, add a mask here.
                        days = n_above.where(n_valid > 0)
                        per_year_counts.append(days.assign_coords(year=yr))

                    if not per_year_counts:
                        ds.close()
                        continue

                    days_yearly = xr.concat(per_year_counts, dim="year")
                    days_mean = days_yearly.mean(dim="year").astype("float32").load()

                    if ens_sum is None:
                        ens_sum = days_mean.copy()
                        ens_n = 1
                    else:
                        if set(days_mean.dims) != set(ens_sum.dims) or any(
                            days_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in days_mean.dims
                        ):
                            days_mean = days_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + days_mean
                        ens_n += 1

                    used_models.append(model)
                    ds.close()

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TXge30 outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"txge30_ensemble_mean_{experiment}_{label}.nc"

            xr.Dataset(
                {"txge30": ensemble_mean.assign_attrs({
                    "units": "days",
                    "long_name": f"Mean days/year with TX ≥ {thr_c} °C",
                })},
                attrs={
                    "title": f"TXge30 Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "threshold_degC": thr_c,
                    "created_by": "txge30_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")

# If you want to run directly with a YAML:
# -------------------------------------------------------
# import yaml, sys
# if __name__ == "__main__":
#     with open(sys.argv[1], "r") as f:
#         cfg = yaml.safe_load(f)
#     run(cfg)