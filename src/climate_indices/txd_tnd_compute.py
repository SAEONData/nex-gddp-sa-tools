#!/usr/bin/env python3
"""
txd_tnd_compute.py
---------------------------------------------------------------
TXdTNd: Count of heatwave EVENTS where BOTH tasmax (TX) and tasmin (TN)
exceed their model-specific 95th percentile thresholds (by calendar day),
with a minimum consecutive length (default: 3 days).

Baseline thresholds are computed once per model from historical (1995–2014),
cached per grid cell as day-of-year percentiles.

Outputs (absolute means within slice):
  data/outputs/txd_tnd/txd_tnd_events_ensemble_mean_<experiment>_<slice>.nc

Optional anomaly (post-process): scenario events − historical baseline events.
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

# ----------------------- helpers ----------------------- #

def _label_slug(s: str) -> str:
    return (s.lower()
            .replace(" ", "_")
            .replace("(", "").replace(")", "")
            .replace("–", "-"))

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
            chunks={"time": -1},  # single time chunk along time
            **_engine_open_kwargs(),
        )
    except Exception:
        return xr.open_mfdataset([str(p) for p in paths], combine="by_coords",
                                 parallel=True, chunks={"time": -1}, use_cftime=True, lock=False)

_year_re = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
def _year_from_name(p: Path) -> Optional[int]:
    m = _year_re.search(p.name)
    return int(m.group()) if m else None

def _files_for_model_exp(data_root: Path, var: str, model: str, experiment: str) -> List[Path]:
    # Expect data/<var>/<model>/<experiment>/*.nc
    hits = sorted((data_root/var/model/experiment).glob("*.nc"))
    if hits:
        return hits
    # fallback: search broadly
    return sorted(p for p in (data_root/var).glob(f"**/{experiment}/*.nc") if model in str(p))

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
        # support cftime calendars
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

def _calendar_pct_doy(da_c: xr.DataArray, q: float, name: str) -> xr.DataArray:
    """Calendar-day percentile over baseline."""
    da_c = da_c.chunk({"time": -1, "lat": 80, "lon": 80})
    doy = da_c["time"].dt.dayofyear
    pct = (
        da_c.assign_coords(dayofyear=doy)
        .groupby("dayofyear")
        .quantile(q, dim="time", skipna=True)
        .astype("float32")
    )
    pct.name = name
    return pct

def _ensure_thr_has_day(thr_doy: xr.DataArray, max_doy: int) -> xr.DataArray:
    have = set(int(v) for v in np.atleast_1d(thr_doy["dayofyear"].values))
    if max_doy == 366 and 366 not in have:
        thr366 = thr_doy.sel(dayofyear=365)
        thr_doy = xr.concat([thr_doy, thr366.assign_coords(dayofyear=366)], dim="dayofyear").sortby("dayofyear")
    return thr_doy

def _count_runs_bool_1d(arr: np.ndarray, min_len: int) -> np.ndarray:
    """Count runs of True (length>=min_len) for a 1D boolean array.
       Returns a scalar as 0D array (to fit apply_ufunc)."""
    if arr.size == 0:
        return np.array(0, dtype=np.int16)
    # run-length encoding on True
    # pad False at both ends to catch edges
    x = np.concatenate([[False], arr.astype(bool), [False]])
    # rising/falling edges
    edges = np.diff(x.astype(np.int8))
    starts = np.where(edges == 1)[0]
    ends   = np.where(edges == -1)[0]
    lengths = ends - starts
    return np.array(np.sum(lengths >= min_len), dtype=np.int16)

# ----------------------- main ----------------------- #

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXdTNd (both TX & TN > 95th pct, consecutive days)…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg       = cfg.get("txd_tnd", {})
    var_tx     = icfg.get("tx_variable", "tasmax")
    var_tn     = icfg.get("tn_variable", "tasmin")
    ref_start  = icfg.get("reference_start", "1995-01-01")
    ref_end    = icfg.get("reference_end",   "2014-12-31")
    pct_q      = float(icfg.get("percentile", 95)) / 100.0
    min_len    = int(icfg.get("min_spell_length", 3))

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "txd_tnd"
    THR_DIR    = OUTPUT_DIR / "_thresholds"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THR_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory (look for either var)
    hist_tx_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / var_tx / "**/historical/*.nc"), recursive=True))
    hist_tn_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / var_tn / "**/historical/*.nc"), recursive=True))

    # Map model -> files
    def _by_model(nc_paths, experiment):
        by = {}
        for p in nc_paths:
            m = _find_model_name(p, experiment)
            by.setdefault(m, []).append(p)
        return by

    tx_hist_by_model = _by_model(hist_tx_files, "historical")
    tn_hist_by_model = _by_model(hist_tn_files, "historical")
    models_hist = sorted(set(tx_hist_by_model) & set(tn_hist_by_model))
    print(f"\n📦 Caching TX/TN {int(pct_q*100)}th pct baselines ({ref_start}–{ref_end}) for {len(models_hist)} models…")

    for model in models_hist:
        cache_tx = THR_DIR / f"{model}_tx{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
        cache_tn = THR_DIR / f"{model}_tn{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
        if cache_tx.exists() and cache_tn.exists():
            continue
        try:
            dtx = _open_mf(tx_hist_by_model[model])
            dtn = _open_mf(tn_hist_by_model[model])

            dtx = _select_bbox(dtx, lat_bounds, lon_bounds)
            dtn = _select_bbox(dtn, lat_bounds, lon_bounds)

            dtx = _slice_time_cf(dtx, ref_start, ref_end)
            dtn = _slice_time_cf(dtn, ref_start, ref_end)

            if var_tx not in dtx or var_tn not in dtn:
                dtx.close(); dtn.close(); continue

            tx_c = convert_units_to(dtx[var_tx], "degC")
            tn_c = convert_units_to(dtn[var_tn], "degC")

            tx95 = _calendar_pct_doy(tx_c, pct_q, "tx95p_doy")
            tn95 = _calendar_pct_doy(tn_c, pct_q, "tn95p_doy")

            xr.Dataset({"tx95p_doy": tx95}).to_netcdf(cache_tx, engine="h5netcdf")
            xr.Dataset({"tn95p_doy": tn95}).to_netcdf(cache_tn, engine="h5netcdf")
            dtx.close(); dtn.close()
            print(f"   ✓ cached {model}")
        except Exception as e:
            print(f"   ⚠️ {model}: {e}")

    thr_cache = {p.name.split("_tx")[0]: p for p in THR_DIR.glob(f"*tx{int(pct_q*100)}p_doy_*.nc")}
    # tn cache will share same model prefix; we’ll open by model each loop.

    # ---- per experiment & time slice ----
    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        tx_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / var_tx / f"**/{experiment}/*.nc"), recursive=True))
        tn_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / var_tn / f"**/{experiment}/*.nc"), recursive=True))
        if not tx_files or not tn_files:
            print("   ⚠️ No files found for TX and/or TN.")
            continue

        tx_by_model = _by_model(tx_files, experiment)
        tn_by_model = _by_model(tn_files, experiment)
        models = sorted(set(tx_by_model) & set(tn_by_model))
        print(f"   Models detected: {len(models)}")

        # Only compute "Baseline" for historical
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.lower().startswith("baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            sY, eY = int(start[:4]), int(end[:4])
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for model in models:
                try:
                    scen_tx = [p for p in tx_by_model[model] if (y := _year_from_name(p)) and (sY <= y <= eY)] or tx_by_model[model]
                    scen_tn = [p for p in tn_by_model[model] if (y := _year_from_name(p)) and (sY <= y <= eY)] or tn_by_model[model]
                    if not scen_tx or not scen_tn:
                        continue

                    ds_tx = _open_mf(scen_tx)
                    ds_tn = _open_mf(scen_tn)

                    ds_tx = _select_bbox(ds_tx, lat_bounds, lon_bounds)
                    ds_tn = _select_bbox(ds_tn, lat_bounds, lon_bounds)

                    ds_tx = _slice_time_cf(ds_tx, start, end)
                    ds_tn = _slice_time_cf(ds_tn, start, end)

                    if var_tx not in ds_tx or var_tn not in ds_tn:
                        ds_tx.close(); ds_tn.close(); continue

                    tx = convert_units_to(ds_tx[var_tx], "degC")
                    tn = convert_units_to(ds_tn[var_tn], "degC")

                    # Load thresholds
                    cache_tx = THR_DIR / f"{model}_tx{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
                    cache_tn = THR_DIR / f"{model}_tn{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
                    if not (cache_tx.exists() and cache_tn.exists()):
                        ds_tx.close(); ds_tn.close(); continue

                    tx95 = xr.open_dataset(cache_tx, engine="h5netcdf")["tx95p_doy"]
                    tn95 = xr.open_dataset(cache_tn, engine="h5netcdf")["tn95p_doy"]

                    # Align grids if necessary
                    if any(tx95.sizes.get(d) != tx.sizes.get(d) for d in ["lat","lon"]):
                        tx95 = tx95.reindex(lat=tx["lat"], lon=tx["lon"], method=None)
                    if any(tn95.sizes.get(d) != tn.sizes.get(d) for d in ["lat","lon"]):
                        tn95 = tn95.reindex(lat=tn["lat"], lon=tn["lon"], method=None)

                    # Match day-of-year thresholds to time
                    doy = tx["time"].dt.dayofyear
                    max_doy = int(doy.max())
                    tx95 = _ensure_thr_has_day(tx95, max_doy)
                    tn95 = _ensure_thr_has_day(tn95, max_doy)

                    thr_tx_t = tx95.sel(dayofyear=doy)
                    thr_tn_t = tn95.sel(dayofyear=doy)

                    hot_mask = (tx > thr_tx_t) & (tn > thr_tn_t)

                    # Count events per year (runs of True with len >= min_len), then mean across years
                    events_per_year = []
                    for yr, grp in hot_mask.groupby("time.year"):
                        # apply along time for each grid cell
                        ev = xr.apply_ufunc(
                            _count_runs_bool_1d, grp,
                            kwargs={"min_len": min_len},
                            input_core_dims=[["time"]],
                            output_core_dims=[[]],
                            vectorize=True,
                            dask="parallelized",
                            output_dtypes=[np.int16],
                        )
                        events_per_year.append(ev.assign_coords(year=yr))

                    if not events_per_year:
                        ds_tx.close(); ds_tn.close(); continue

                    ev_yearly = xr.concat(events_per_year, dim="year")
                    ev_mean = ev_yearly.mean(dim="year").astype("float32").load()

                    if ens_sum is None:
                        ens_sum = ev_mean.copy()
                        ens_n = 1
                    else:
                        if set(ev_mean.dims) != set(ens_sum.dims) or any(
                            ev_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in ev_mean.dims
                        ):
                            ev_mean = ev_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + ev_mean
                        ens_n += 1

                    used_models.append(model)
                    ds_tx.close(); ds_tn.close()

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TXdTNd outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            
            out_nc = OUTPUT_DIR / f"txd_tnd_ensemble_mean_{experiment}_{label}.nc"

            xr.Dataset(
                {"txd_tnd_events": ensemble_mean.assign_attrs({
                    "units": "events",
                    "long_name": f"Heatwave events (runs ≥{min_len} days) with TX & TN > {int(pct_q*100)}th pct (baseline)",
                    "threshold_reference": f"{ref_start} to {ref_end}",
                    "definition_note": "Events counted per year, then averaged within slice.",
                })},
                attrs={
                    "title": f"TXdTNd Events — Ensemble Mean — {experiment} — {slice_name}",
                    "created_by": "txd_tnd_compute.py",
                    "models_included": ", ".join(sorted(set(used_models))),
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")