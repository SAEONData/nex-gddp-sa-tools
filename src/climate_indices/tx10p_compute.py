#!/usr/bin/env python3
"""
tx10p_compute.py (single-file-per-model, segfault-safe)
---------------------------------------------------------------
TX10p: Percent of days with tasmax BELOW the model-specific
10th percentile threshold by calendar day, where the threshold
is computed from the historical baseline (1995–2014) per model.

Inputs (combined, one file per model+experiment):
  data/combined/tasmax/<experiment>/tasmax_<MODEL>_<experiment>.nc

Outputs:
  data/outputs/tx10p/tx10p_ensemble_mean_<experiment>_<slice>.nc
Caches:
  data/outputs/tx10p/_thresholds/<MODEL>_tx10p_1995-2014.nc
"""

from __future__ import annotations
import os, re, glob, time, warnings
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

# ───────────────────── runtime guardrails ─────────────────────
warnings.filterwarnings("ignore")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("XR_USE_FLOX", "0")              # simpler groupby backend
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

try:
    import dask
    dask.config.set({
        "array.slicing.split_large_chunks": True,
        "scheduler": "single-threaded",    # avoid threaded HDF5/NetCDF IO
    })
except Exception:
    pass

# ----------------------- helpers ----------------------- #

def _label_slug(s: str) -> str:
    return (s.lower()
            .replace(" ", "_")
            .replace("(", "").replace(")", "")
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

def _engine_open_kwargs():
    # h5netcdf is usually the most stable for these files
    return dict(engine="h5netcdf", use_cftime=True, decode_times=True,
                chunks={"time": -1}, mask_and_scale=True, lock=False)

def _open_single(path: Path) -> xr.Dataset:
    """Open one combined file robustly (no mfdataset, cftime-safe)."""
    # Primary path: h5netcdf
    try:
        return xr.open_dataset(str(path), **_engine_open_kwargs())
    except Exception:
        # Fallbacks: scipy with/without decode
        for kw in [
            dict(engine="scipy", decode_times=True),
            dict(engine="scipy", decode_times=False),
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
    # Expect: tasmax_<MODEL>_<experiment>.nc (MODEL may have hyphens/underscores)
    toks = p.stem.split("_")
    if len(toks) >= 3 and toks[-1] == experiment:
        return "_".join(toks[1:-1])
    # fallback: folder name just before experiment directory
    try:
        idx = p.parts.index(experiment)
        if idx > 0:
            return p.parts[idx - 1]
    except ValueError:
        pass
    return "unknown_model"

def _list_models(exp_dir: Path, experiment: str) -> Dict[str, Path]:
    """Return {model: file} for combined files in <var>/<experiment>."""
    mapping = {}
    if not exp_dir.exists():
        return mapping
    for f in sorted(exp_dir.glob("*.nc")):
        m = _model_from_filename(f, experiment)
        mapping[m] = f
    return mapping

_year_re = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
def _year_from_name(p: Path) -> Optional[int]:
    m = _year_re.search(p.name)
    return int(m.group()) if m else None

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    """Slice using dataset’s own calendar dtype (cftime- and numpy-safe)."""
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

def _calendar10p_threshold(tx_hist_c: xr.DataArray) -> xr.DataArray:
    """TX10p (10th percentile by calendar day) on baseline (cftime-safe)."""
    tx_hist_c = tx_hist_c.chunk({"time": -1, "lat": 80, "lon": 80})
    doy = tx_hist_c["time"].dt.dayofyear
    thr = (
        tx_hist_c.assign_coords(dayofyear=doy)
        .groupby("dayofyear")
        .quantile(0.10, dim="time", skipna=True)
    )
    thr.name = "tx10p"
    thr.attrs.update({"units": "degC"})
    return thr

def _ensure_thr_has_day(thr_doy: xr.DataArray, max_doy: int) -> xr.DataArray:
    """If scenario has day 366 and thr lacks it, copy day 365."""
    have = set(int(v) for v in np.atleast_1d(thr_doy["dayofyear"].values))
    if max_doy == 366 and 366 not in have:
        thr366 = thr_doy.sel(dayofyear=365)
        thr_doy = xr.concat([thr_doy, thr366.assign_coords(dayofyear=366)],
                            dim="dayofyear").sortby("dayofyear")
    return thr_doy

# ----------------------- main ----------------------- #

def run(cfg):
    t0 = time.time()
    print("🧮 Starting TX10p (baseline by model, single-file inputs)…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    icfg      = cfg.get("tx10p", {})
    varname   = icfg.get("variable", "tasmax")
    ref_start = icfg.get("reference_start", "1995-01-01")
    ref_end   = icfg.get("reference_end",   "2014-12-31")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths (combined inputs)
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "tx10p"
    THR_DIR    = OUTPUT_DIR / "_thresholds"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THR_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory combined files per experiment: {experiment -> {model: file}}
    exp_maps: Dict[str, Dict[str, Path]] = {}
    for exp in experiments:
        exp_dir = DATA_DIR / exp
        exp_maps[exp] = _list_models(exp_dir, exp)
        print(f"📁 {exp}: {len(exp_maps[exp])} combined files")

    # ---- 1) Cache TX10p per model from historical (once) ----
    hist_models = sorted(exp_maps.get("historical", {}).keys())
    print(f"\n📦 Caching TX10p (baseline {ref_start}–{ref_end}) for {len(hist_models)} models…")

    for model in hist_models:
        cache_path = THR_DIR / f"{model}_tx10p_{ref_start[:4]}-{ref_end[:4]}.nc"
        if cache_path.exists():
            continue
        f_hist = exp_maps["historical"].get(model)
        if f_hist is None:
            continue

        ds = None
        try:
            ds = _open_single(f_hist)
            if varname not in ds:
                continue
            ds = _select_bbox(ds, lat_bounds, lon_bounds)
            ds = _slice_time_cf(ds, ref_start, ref_end)
            if ds.sizes.get("time", 0) == 0:
                continue

            tx_hist_c = convert_units_to(ds[varname], "degC")
            thr_doy = _calendar10p_threshold(tx_hist_c).astype("float32")

            xr.Dataset({"tx10p": thr_doy}).to_netcdf(cache_path, engine="h5netcdf")
            print(f"   ✓ cached {model}")
        except Exception as e:
            print(f"   ⚠️ {model}: {e}")
        finally:
            try:
                if ds is not None:
                    ds.close()
            except Exception:
                pass

    thr_cache = {p.name.split("_tx10p_")[0]: p for p in THR_DIR.glob("*_tx10p_*.nc")}

    # ---- 2) Per experiment & slice, ensemble of % days < TX10p ----
    for experiment in experiments:
        model_files = exp_maps.get(experiment, {})
        if not model_files:
            print(f"\n📁 {experiment}: no files.")
            continue

        print(f"\n📁 Experiment: {experiment}  (models: {len(model_files)})")
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

            for model, scen_file in sorted(model_files.items()):
                cache_path = thr_cache.get(model)
                if cache_path is None:
                    continue

                ds = thr_ds = None
                try:
                    ds = _open_single(scen_file)
                    if varname not in ds:
                        continue

                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = _slice_time_cf(ds, start, end)
                    if ds.sizes.get("time", 0) == 0:
                        continue

                    tasmax = convert_units_to(ds[varname], "degC")

                    thr_ds = xr.open_dataset(cache_path, engine="h5netcdf")
                    tx10p = thr_ds["tx10p"]

                    # Align grid if needed
                    if ("lat" in tx10p.coords and "lat" in tasmax.coords
                        and "lon" in tx10p.coords and "lon" in tasmax.coords):
                        if (tx10p.sizes.get("lat") != tasmax.sizes.get("lat")
                            or tx10p.sizes.get("lon") != tasmax.sizes.get("lon")):
                            tx10p = tx10p.reindex(lat=tasmax["lat"], lon=tasmax["lon"], method=None)

                    doy = tasmax["time"].dt.dayofyear
                    max_doy = int(doy.max())
                    tx10p = _ensure_thr_has_day(tx10p, max_doy)
                    thr_t = tx10p.sel(dayofyear=doy)

                    below = (tasmax < thr_t).chunk({"time": -1})

                    # % days per year then mean across years
                    per_year_pct = []
                    for yr, grp in below.groupby("time.year"):
                        grp = grp.chunk({"time": -1})
                        valid = grp.notnull()
                        n_total = valid.sum(dim="time")
                        n_below = grp.sum(dim="time")
                        pct = (100.0 * n_below / n_total).where(n_total > 0)
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
                finally:
                    try:
                        if ds is not None: ds.close()
                    except Exception:
                        pass
                    try:
                        if thr_ds is not None: thr_ds.close()
                    except Exception:
                        pass

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid TX10p outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = OUTPUT_DIR / f"tx10p_ensemble_mean_{experiment}_{label}.nc"

            xr.Dataset(
                {"tx10p": ensemble_mean.assign_attrs({
                    "units": "percent",
                    "long_name": "Percent of days with TX < baseline TX10p (by day-of-year)",
                    "threshold_reference": f"{ref_start} to {ref_end}",
                })},
                attrs={
                    "title": f"TX10p Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "created_by": "tx10p_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")