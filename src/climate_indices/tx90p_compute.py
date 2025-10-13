#!/usr/bin/env python3
"""
tx90p_compute.py (single-file-per-model, cftime-safe)
---------------------------------------------------------------
TX90p: Percent of days with tasmax ABOVE the model-specific
90th percentile threshold by calendar day, where the threshold
is computed from the historical baseline (1995–2014) per model.

Inputs (combined, one file per model+experiment):
  data/combined/tasmax/<experiment>/tasmax_<MODEL>_<experiment>.nc

Outputs:
  data/outputs/tx90p/tx90p_ensemble_mean_<experiment>_<slice>.nc
Caches:
  data/outputs/tx90p/_thresholds/<MODEL>_tx90p_1995-2014.nc
"""

from __future__ import annotations
import os, glob, time, warnings
from pathlib import Path
from typing import Dict, Tuple, List

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

# ───────────────── runtime guards ─────────────────
warnings.filterwarnings("ignore")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("XR_USE_FLOX", "0")  # simpler groupby backend

try:
    import dask
    dask.config.set({
        "array.slicing.split_large_chunks": True,
        "scheduler": "single-threaded",    # avoid HDF5 threading crashes
    })
except Exception:
    pass

# ───────────────── helpers ─────────────────

def _label_slug(s: str) -> str:
    return (s.lower().replace(" ", "_").replace("(", "").replace(")", "")
                 .replace("–", "-").replace("/", "-"))

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
    """Open one combined file robustly (no mfdataset, cftime-safe)."""
    for kw in [
        dict(engine="h5netcdf", use_cftime=True, decode_times=True, chunks={"time": -1}, mask_and_scale=True, lock=False),
        dict(engine="scipy",     use_cftime=True, decode_times=True, chunks={"time": -1}),
        dict(engine="scipy",     use_cftime=True, decode_times=False, chunks={"time": -1}),  # decode later
    ]:
        try:
            ds = xr.open_dataset(str(path), **kw)
            if not kw.get("decode_times", True):
                try:
                    ds = xr.decode_cf(ds)
                except Exception:
                    pass
            return ds
        except Exception:
            continue
    raise IOError(f"Could not open: {path}")

def _model_from_filename(p: Path, experiment: str) -> str:
    # Expect: tasmax_<MODEL>_<experiment>.nc
    toks = p.stem.split("_")
    if len(toks) >= 3 and toks[-1] == experiment:
        return "_".join(toks[1:-1])  # model may contain hyphens/underscores
    # fallback: folder name before experiment
    try:
        idx = p.parts.index(experiment)
        if idx > 0:
            return p.parts[idx - 1]
    except ValueError:
        pass
    return "unknown_model"

def _list_models(exp_dir: Path, experiment: str) -> Dict[str, Path]:
    """Return {model: file} mapping for combined files in a given experiment dir."""
    mapping = {}
    for f in sorted(exp_dir.glob("*.nc")):
        m = _model_from_filename(f, experiment)
        mapping[m] = f
    return mapping

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    """Slice with dataset's own calendar dtype."""
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

def _calendar90p_threshold(tx_hist_c: xr.DataArray) -> xr.DataArray:
    """TX90p (90th percentile by calendar day) on baseline (cftime-safe)."""
    tx_hist_c = tx_hist_c.chunk({"time": -1, "lat": 80, "lon": 80})
    thr = (
        tx_hist_c
        .assign_coords(dayofyear=tx_hist_c["time"].dt.dayofyear)
        .groupby("dayofyear")
        .quantile(0.90, dim="time", skipna=True)
        .rename("tx90p")
    )
    thr.attrs["units"] = "degC"
    return thr

def _ensure_thr_has_day(thr_doy: xr.DataArray, max_doy: int) -> xr.DataArray:
    """If scenario has day 366 and thr lacks it, copy day 365."""
    have = set(int(v) for v in np.atleast_1d(thr_doy["dayofyear"].values))
    if max_doy == 366 and 366 not in have:
        thr366 = thr_doy.sel(dayofyear=365)
        thr_doy = xr.concat([thr_doy, thr366.assign_coords(dayofyear=366)], dim="dayofyear").sortby("dayofyear")
    return thr_doy

# ───────────────── main ─────────────────

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TX90p (per-model cached thresholds, single-file inputs)…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg      = cfg.get("tx90p", {})
    varname   = icfg.get("variable", "tasmax")
    ref_start = icfg.get("reference_start", "1995-01-01")
    ref_end   = icfg.get("reference_end",   "2014-12-31")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths (combined inputs)
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "tx90p"
    THR_DIR    = OUTPUT_DIR / "_thresholds"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THR_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory: {experiment -> {model: file}}
    exp_maps: Dict[str, Dict[str, Path]] = {}
    for exp in experiments:
        exp_dir = DATA_DIR / exp
        exp_maps[exp] = _list_models(exp_dir, exp) if exp_dir.exists() else {}
        print(f"📁 {exp}: {len(exp_maps[exp])} combined files")

    # ---------- 1) Cache TX90p per model from historical ----------
    hist_models = sorted(exp_maps.get("historical", {}).keys())
    print(f"\n📦 Caching TX90p (baseline {ref_start}–{ref_end}) for {len(hist_models)} models…")

    for model in hist_models:
        cache_path = THR_DIR / f"{model}_tx90p_{ref_start[:4]}-{ref_end[:4]}.nc"
        if cache_path.exists():
            continue
        f_hist = exp_maps["historical"].get(model)
        if f_hist is None:
            continue
        try:
            with _open_single(f_hist) as dsh:
                if varname not in dsh:
                    continue
                dsh = _select_bbox(dsh, lat_bounds, lon_bounds)
                dsh = _slice_time_cf(dsh, ref_start, ref_end)
                if dsh.sizes.get("time", 0) == 0:
                    continue

                tx_hist_c = convert_units_to(dsh[varname], "degC")
                thr_doy = _calendar90p_threshold(tx_hist_c).astype("float32")
                xr.Dataset({"tx90p": thr_doy}).to_netcdf(cache_path, engine="h5netcdf")
                print(f"   ✓ cached {model}")
        except Exception as e:
            print(f"   ⚠️ {model}: {e}")

    # Map model -> cached threshold path
    thr_cache = {p.name.split("_tx90p_")[0]: p for p in THR_DIR.glob("*_tx90p_*.nc")}

    # ---------- 2) Ensemble per experiment / time slice ----------
    for experiment in experiments:
        model_files = exp_maps.get(experiment, {})
        if not model_files:
            print(f"\n📁 {experiment}: no files.")
            continue

        print(f"\n📁 Experiment: {experiment}  (models: {len(model_files)})")
        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            if not start or not end:
                continue
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for model, scen_file in sorted(model_files.items()):
                cache_path = thr_cache.get(model)
                if cache_path is None:
                    continue  # no baseline TX90p for this model

                try:
                    with _open_single(scen_file) as ds, xr.open_dataset(cache_path, engine="h5netcdf") as thr_ds:
                        if varname not in ds:
                            continue

                        ds = _select_bbox(ds, lat_bounds, lon_bounds)
                        ds = _slice_time_cf(ds, start, end)
                        if ds.sizes.get("time", 0) == 0:
                            continue

                        tx_scen_c = convert_units_to(ds[varname], "degC")
                        tx90p = thr_ds["tx90p"]

                        # Align threshold grid if needed (lat/lon only)
                        if ("lat" in tx90p.coords and "lat" in tx_scen_c.coords
                            and "lon" in tx90p.coords and "lon" in tx_scen_c.coords):
                            if (tx90p.sizes.get("lat") != tx_scen_c.sizes.get("lat")
                                or tx90p.sizes.get("lon") != tx_scen_c.sizes.get("lon")):
                                tx90p = tx90p.reindex(lat=tx_scen_c["lat"], lon=tx_scen_c["lon"], method=None)

                        scen_doy = tx_scen_c["time"].dt.dayofyear
                        max_doy = int(scen_doy.max())
                        tx90p = _ensure_thr_has_day(tx90p, max_doy)
                        thr_t = tx90p.sel(dayofyear=scen_doy)

                        above = (tx_scen_c > thr_t).chunk({"time": -1})  # daily bool, single time chunk

                        # % days per year then mean across years
                        per_year_pct = []
                        for yr, grp in above.groupby("time.year"):
                            grp = grp.chunk({"time": -1})
                            valid = grp.notnull()
                            n_total = valid.sum(dim="time")
                            n_above = grp.sum(dim="time")
                            pct = (100.0 * n_above / n_total).where(n_total > 0)
                            per_year_pct.append(pct.assign_coords(year=yr))

                        if not per_year_pct:
                            continue

                        pct_yearly = xr.concat(per_year_pct, dim="year")
                        pct_mean = pct_yearly.mean(dim="year").astype("float32").load()

                        if ens_sum is None:
                            ens_sum = pct_mean.copy(); ens_n = 1
                        else:
                            if set(pct_mean.dims) != set(ens_sum.dims) or any(
                                pct_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in pct_mean.dims
                            ):
                                pct_mean = pct_mean.reindex_like(ens_sum)
                            ens_sum = ens_sum + pct_mean; ens_n += 1

                        used_models.append(model)

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TX90p outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = (OUTPUT_DIR / f"tx90p_ensemble_mean_{experiment}_{label}.nc")

            xr.Dataset(
                {"tx90p": ensemble_mean.assign_attrs({
                    "units": "percent",
                    "long_name": "Percent of days with TX > baseline TX90p (by day-of-year)",
                    "threshold_reference": f"{ref_start} to {ref_end}",
                })},
                attrs={
                    "title": f"TX90p Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "created_by": "tx90p_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")