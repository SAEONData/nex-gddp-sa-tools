#!/usr/bin/env python3
"""
txd_tnd_compute.py
---------------------------------------------------------------
TXdTNd: Count of heatwave EVENTS where BOTH tasmax (TX) and tasmin (TN)
exceed their model-specific {percentile}th percentile thresholds (by calendar day),
with a minimum consecutive length (default: 3 days).

Baseline thresholds are computed once per model from historical (1995–2014),
cached per grid cell as day-of-year percentiles.

Outputs (events/year mean within slice, ensemble-mean across models):
  data/outputs/txd_tnd/txd_tnd_ensemble_mean_<experiment>_<slice>.nc
"""

from __future__ import annotations
import os, glob, time, warnings
from pathlib import Path
from typing import Dict, Tuple, List

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

# ───────── runtime guards ─────────
warnings.filterwarnings("ignore")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("XR_USE_FLOX", "0")

try:
    import dask
    dask.config.set({"array.slicing.split_large_chunks": True})
except Exception:
    pass

# ───────── helpers ─────────

def _label_slug(s: str) -> str:
    return (s.lower()
            .replace(" ", "_")
            .replace("(", "").replace(")", "")
            .replace("–", "-").replace("—", "-")
            .replace("/", "-"))

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    if "lat" not in ds.coords or "lon" not in ds.coords:
        return ds
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _open_single(path: Path) -> xr.Dataset:
    """Open one combined file robustly (cftime-safe, avoid netcdf4)."""
    for kw in [
        dict(engine="h5netcdf", use_cftime=True, decode_times=True,
             chunks={"time": -1}, mask_and_scale=True, lock=False),
        dict(engine="h5netcdf", use_cftime=True, decode_times=False,
             chunks={"time": -1}, mask_and_scale=True, lock=False),
    ]:
        try:
            ds = xr.open_dataset(str(path), **kw)
            if kw.get("decode_times") is False:
                try:
                    ds = xr.decode_cf(ds, use_cftime=True)
                except Exception:
                    pass
            return ds
        except Exception:
            continue
    raise IOError(f"Could not open: {path}")

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    """Slice using dataset's calendar type; safe for cftime & numpy datetime64."""
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

def _calendar_pct_doy(da_c: xr.DataArray, q: float, name: str) -> xr.DataArray:
    """Calendar-day percentile over baseline (dayofyear)."""
    da_c = da_c.chunk({"time": -1, "lat": 80, "lon": 80})
    doy = da_c["time"].dt.dayofyear
    pct = (
        da_c.assign_coords(dayofyear=doy)
        .groupby("dayofyear")
        .quantile(q, dim="time", skipna=True)
        .astype("float32")
        .rename(name)
    )
    pct.attrs["units"] = da_c.attrs.get("units", "")
    return pct

def _ensure_thr_has_day(thr_doy: xr.DataArray, max_doy: int) -> xr.DataArray:
    """If scenario has day 366 and thr lacks it, copy day 365."""
    have = set(int(v) for v in np.atleast_1d(thr_doy["dayofyear"].values))
    if max_doy == 366 and 366 not in have:
        thr366 = thr_doy.sel(dayofyear=365)
        thr_doy = xr.concat([thr_doy, thr366.assign_coords(dayofyear=366)], dim="dayofyear").sortby("dayofyear")
    return thr_doy

def _count_runs_bool_1d(arr: np.ndarray, min_len: int) -> np.ndarray:
    """Count runs of True (length>=min_len) for a 1D boolean array.
       Returns scalar in a 0D array (for apply_ufunc)."""
    if arr.size == 0:
        return np.array(0, dtype=np.int16)
    x = np.concatenate([[False], arr.astype(bool), [False]])
    edges = np.diff(x.astype(np.int8))
    starts = np.where(edges == 1)[0]
    ends   = np.where(edges == -1)[0]
    lengths = ends - starts
    return np.array(np.sum(lengths >= min_len), dtype=np.int16)

def _model_from_filename(p: Path, experiment: str) -> str:
    # Expect: tasmax_<MODEL>_<experiment>.nc  (or tasmin_...)
    toks = p.stem.split("_")
    if len(toks) >= 3 and toks[-1] == experiment:
        return "_".join(toks[1:-1])
    try:
        idx = p.parts.index(experiment)
        if idx > 0:
            return p.parts[idx - 1]
    except ValueError:
        pass
    return "unknown_model"


# ───────── main ─────────

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TXdTNd (TX & TN > pct threshold, consecutive days)…")

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

    # Paths (COMBINED inputs)
    ROOT        = Path(__file__).resolve().parents[2]
    DATA_TX_DIR = ROOT / "data" / "combined" / var_tx
    DATA_TN_DIR = ROOT / "data" / "combined" / var_tn
    OUTPUT_DIR  = ROOT / "data" / "outputs" / "txd_tnd"
    THR_DIR     = OUTPUT_DIR / "_thresholds"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THR_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 1) Cache DOY thresholds per model from historical ----------
    hist_tx = sorted(Path(p).resolve() for p in glob.glob(str(DATA_TX_DIR / "historical" / "*.nc")))
    hist_tn = sorted(Path(p).resolve() for p in glob.glob(str(DATA_TN_DIR / "historical" / "*.nc")))

    tx_models = {_model_from_filename(p, "historical"): p for p in hist_tx}
    tn_models = {_model_from_filename(p, "historical"): p for p in hist_tn}
    common_models = sorted(set(tx_models) & set(tn_models))

    print(f"\n📦 Caching {int(pct_q*100)}th pct DOY baselines ({ref_start[:4]}–{ref_end[:4]}) for {len(common_models)} models…")

    for model in common_models:
        cache_tx = THR_DIR / f"{model}_tx{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
        cache_tn = THR_DIR / f"{model}_tn{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
        if cache_tx.exists() and cache_tn.exists():
            continue
        try:
            with _open_single(tx_models[model]) as ds_tx, _open_single(tn_models[model]) as ds_tn:
                ds_tx = _select_bbox(ds_tx, lat_bounds, lon_bounds)
                ds_tn = _select_bbox(ds_tn, lat_bounds, lon_bounds)

                ds_tx = _slice_time_cf(ds_tx, ref_start, ref_end)
                ds_tn = _slice_time_cf(ds_tn, ref_start, ref_end)

                if var_tx not in ds_tx or var_tn not in ds_tn or ds_tx.sizes.get("time", 0) == 0 or ds_tn.sizes.get("time", 0) == 0:
                    continue

                tx_c = convert_units_to(ds_tx[var_tx], "degC")
                tn_c = convert_units_to(ds_tn[var_tn], "degC")

                tx_pct = _calendar_pct_doy(tx_c, pct_q, f"tx{int(pct_q*100)}p_doy")
                tn_pct = _calendar_pct_doy(tn_c, pct_q, f"tn{int(pct_q*100)}p_doy")

                xr.Dataset({tx_pct.name: tx_pct}).to_netcdf(cache_tx, engine="h5netcdf")
                xr.Dataset({tn_pct.name: tn_pct}).to_netcdf(cache_tn, engine="h5netcdf")
                print(f"   ✓ cached {model}")
        except Exception as e:
            print(f"   ⚠️ {model}: {e}")

    # ---------- 2) Per experiment/slice: count events & ensemble ----------
    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        tx_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_TX_DIR / experiment / "*.nc")))
        tn_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_TN_DIR / experiment / "*.nc")))
        if not tx_files or not tn_files:
            print("   ⚠️ No files found for TX and/or TN.")
            continue

        tx_map = {_model_from_filename(p, experiment): p for p in tx_files}
        tn_map = {_model_from_filename(p, experiment): p for p in tn_files}
        models = sorted(set(tx_map) & set(tn_map))
        print(f"   Models detected: {len(models)}")

        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.lower().startswith("baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            if not start or not end:
                continue
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for model in models:
                cache_tx = THR_DIR / f"{model}_tx{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
                cache_tn = THR_DIR / f"{model}_tn{int(pct_q*100)}p_doy_{ref_start[:4]}-{ref_end[:4]}.nc"
                if not (cache_tx.exists() and cache_tn.exists()):
                    continue

                try:
                    with _open_single(tx_map[model]) as ds_tx, _open_single(tn_map[model]) as ds_tn, \
                         xr.open_dataset(cache_tx, engine="h5netcdf") as thr_tx_ds, \
                         xr.open_dataset(cache_tn, engine="h5netcdf") as thr_tn_ds:

                        ds_tx = _select_bbox(ds_tx, lat_bounds, lon_bounds)
                        ds_tn = _select_bbox(ds_tn, lat_bounds, lon_bounds)

                        ds_tx = _slice_time_cf(ds_tx, start, end)
                        ds_tn = _slice_time_cf(ds_tn, start, end)

                        if var_tx not in ds_tx or var_tn not in ds_tn or ds_tx.sizes.get("time", 0) == 0 or ds_tn.sizes.get("time", 0) == 0:
                            continue

                        tx = convert_units_to(ds_tx[var_tx], "degC").chunk({"time": -1})
                        tn = convert_units_to(ds_tn[var_tn], "degC").chunk({"time": -1})

                        txp = thr_tx_ds[f"tx{int(pct_q*100)}p_doy"]
                        tnp = thr_tn_ds[f"tn{int(pct_q*100)}p_doy"]

                        # Align grids if necessary
                        if ("lat" in tx.coords and "lon" in tx.coords and
                            ("lat" in txp.coords and "lon" in txp.coords)):
                            if txp.sizes.get("lat") != tx.sizes.get("lat") or txp.sizes.get("lon") != tx.sizes.get("lon"):
                                txp = txp.reindex(lat=tx["lat"], lon=tx["lon"], method=None)
                        if ("lat" in tn.coords and "lon" in tn.coords and
                            ("lat" in tnp.coords and "lon" in tnp.coords)):
                            if tnp.sizes.get("lat") != tn.sizes.get("lat") or tnp.sizes.get("lon") != tn.sizes.get("lon"):
                                tnp = tnp.reindex(lat=tn["lat"], lon=tn["lon"], method=None)

                        # Match DOY thresholds to timeline
                        doy = tx["time"].dt.dayofyear
                        max_doy = int(doy.max())
                        txp = _ensure_thr_has_day(txp, max_doy)
                        tnp = _ensure_thr_has_day(tnp, max_doy)
                        thr_tx_t = txp.sel(dayofyear=doy)
                        thr_tn_t = tnp.sel(dayofyear=doy)

                        hot = (tx > thr_tx_t) & (tn > thr_tn_t)
                        hot = hot.chunk({"time": -1})

                        # Count EVENTS per year, then average years
                        per_year = []
                        for yr, grp in hot.groupby("time.year"):
                            ev = xr.apply_ufunc(
                                _count_runs_bool_1d, grp,
                                kwargs={"min_len": min_len},
                                input_core_dims=[["time"]],
                                output_core_dims=[[]],
                                vectorize=True,
                                dask="parallelized",
                                output_dtypes=[np.int16],
                            )
                            per_year.append(ev.assign_coords(year=yr))

                        if not per_year:
                            continue

                        ev_yearly = xr.concat(per_year, dim="year")
                        ev_mean = ev_yearly.mean(dim="year").astype("float32").load()

                        if ens_sum is None:
                            ens_sum = ev_mean.copy(); ens_n = 1
                        else:
                            if set(ev_mean.dims) != set(ens_sum.dims) or any(
                                ev_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in ev_mean.dims
                            ):
                                ev_mean = ev_mean.reindex_like(ens_sum)
                            ens_sum = ens_sum + ev_mean; ens_n += 1

                        used_models.append(model)

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
                    "units": "events per year",
                    "long_name": (
                        f"Mean yearly count of events (runs ≥{min_len} days) with "
                        f"TX & TN > {int(pct_q*100)}th percentile by calendar day"
                    ),
                    "threshold_reference": f"{ref_start} to {ref_end}",
                })},
                attrs={
                    "title": f"TXdTNd Events — Ensemble Mean — {experiment} — {slice_name}",
                    "created_by": "txd_tnd_compute.py",
                    "models_included": ", ".join(sorted(set(used_models))),
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")