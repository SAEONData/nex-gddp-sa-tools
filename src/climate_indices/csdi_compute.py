#!/usr/bin/env python3
"""
csdi_compute.py
---------------------------------------------------------------
CSDI (Cold Spell Duration Index):
Days belonging to spells of >= min_spell_length consecutive days
with tasmin < the MODEL-SPECIFIC TN10p (10th pct by calendar day),
where TN10p is computed from that model's historical baseline
(1995–2014 by default) and cached to disk.

Outputs
  data/outputs/csdi/csdi_ensemble_mean_<experiment>_<slice>.nc
Caches
  data/outputs/csdi/_thresholds/<MODEL>_tn10p_1995-2014.nc
"""

from __future__ import annotations
import os, re, glob, time, warnings
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np
import xarray as xr
from xclim.core.units import convert_units_to

# ── runtime settings to avoid crashes/warnings and keep memory tame ──
warnings.filterwarnings("ignore")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")  # reduce HDF5 lock issues
os.environ.setdefault("XR_USE_FLOX", "0")                # use blockwise for groupby-quantile

try:
    import dask
    dask.config.set({"array.slicing.split_large_chunks": True})
except Exception:
    pass

# ───────────────────────── helpers ───────────────────────── #

def _label_slug(s: str) -> str:
    return (s.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("–", "-"))

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    if "lat" not in ds.coords or "lon" not in ds.coords:
        raise ValueError("Dataset missing 'lat' and/or 'lon'.")
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    lat_slice = slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0])
    lon_slice = slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0])
    return ds.sel(lat=lat_slice, lon=lon_slice)

def _engine_open_kwargs():
    return dict(engine="h5netcdf", use_cftime=True, lock=False)

def _is_healthy_file(p: Path) -> bool:
    try:
        ds = xr.open_dataset(str(p), chunks={}, **_engine_open_kwargs())
        ds.close()
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
            chunks={"time": -1},  # one time chunk -> blockwise quantile ok
            **_engine_open_kwargs(),
        )
    except Exception:
        return xr.open_mfdataset(
            [str(p) for p in paths],
            combine="by_coords",
            parallel=True,
            chunks={"time": -1},
            use_cftime=True,
            lock=False,
        )

_year_re = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
def _year_from_name(p: Path) -> Optional[int]:
    m = _year_re.search(p.name)
    return int(m.group()) if m else None

def _files_for_model_exp(data_dir: Path, model: str, experiment: str) -> List[Path]:
    hits = sorted(data_dir.glob(f"**/{model}/{experiment}/*.nc"))
    if hits:
        return hits
    hits = [p for p in data_dir.glob(f"**/{experiment}/*.nc") if model in str(p)]
    return sorted(hits)

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
    return sorted({ _find_model_name(p, experiment) for p in files })

def _slice_time_cf(ds: xr.Dataset, start: str, end: str) -> xr.Dataset:
    if "time" not in ds or ds.sizes.get("time", 0) == 0:
        return ds
    t0 = ds["time"].values[0]
    if isinstance(t0, np.datetime64):
        s = np.datetime64(start); e = np.datetime64(end)
    else:
        cls = type(t0)
        y0, m0, d0 = (int(start[:4]), int(start[5:7]), int(start[8:10]))
        y1, m1, d1 = (int(end[:4]), int(end[5:7]), int(end[8:10]))
        try:
            s = cls(y0, m0, d0); e = cls(y1, m1, d1)
        except TypeError:
            import cftime
            s = cftime.DatetimeProlepticGregorian(y0, m0, d0)
            e = cftime.DatetimeProlepticGregorian(y1, m1, d1)
    tmin = ds["time"].values[0]; tmax = ds["time"].values[-1]
    s = s if s > tmin else tmin
    e = e if e < tmax else tmax
    if e < s:
        return ds.isel(time=slice(0, 0))
    return ds.sel(time=slice(s, e))

def _calendar10p_threshold(tas_hist_c: xr.DataArray) -> xr.DataArray:
    """TN10p (10th percentile by calendar day) on baseline (cftime-safe)."""
    tas_hist_c = tas_hist_c.chunk({"time": -1, "lat": 80, "lon": 80})
    doy = tas_hist_c["time"].dt.dayofyear
    thr = (
        tas_hist_c
        .assign_coords(dayofyear=doy)
        .groupby("dayofyear")
        .quantile(0.1, dim="time", skipna=True)
    )
    thr.name = "tn10p"
    thr.attrs.update({"units": "degC"})
    return thr

def _ensure_thr_has_day(thr_doy: xr.DataArray, max_doy: int) -> xr.DataArray:
    have = set(int(v) for v in np.atleast_1d(thr_doy["dayofyear"].values))
    if max_doy == 366 and 366 not in have:
        thr366 = thr_doy.sel(dayofyear=365)
        thr_doy = xr.concat([thr_doy, thr366.assign_coords(dayofyear=366)], dim="dayofyear").sortby("dayofyear")
    return thr_doy

def _csdi_days_in_runs_1d(x: np.ndarray, runlen: int) -> np.ndarray:
    """Count True days that belong to runs >= runlen in 1D array."""
    if x.size == 0:
        return np.array(0, dtype=np.int32)
    x = np.asarray(x, dtype=np.bool_)
    diff = np.diff(x.astype(np.int8), prepend=0, append=0)
    starts = np.nonzero(diff == 1)[0]
    ends   = np.nonzero(diff == -1)[0] - 1
    total = 0
    for s, e in zip(starts, ends):
        L = e - s + 1
        if L >= runlen:
            total += L
    return np.array(total, dtype=np.int32)

def _csdi_days_per_year(bool_da: xr.DataArray, runlen: int) -> xr.DataArray:
    return xr.apply_ufunc(
        _csdi_days_in_runs_1d,
        bool_da,
        kwargs={"runlen": runlen},
        input_core_dims=[["time"]],
        output_core_dims=[[]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[np.int32],
    )

# ───────────────────────── main ───────────────────────── #

def run(cfg):
    t0 = time.time()
    print("🧮 Starting CSDI (per-model cached thresholds, streaming ensemble)…")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    csdi_cfg  = cfg.get("csdi", {})
    varname   = csdi_cfg.get("variable", "tasmin")           # TN
    runlen    = int(csdi_cfg.get("min_spell_length", 6))
    ref_start = csdi_cfg.get("reference_start", "1995-01-01")
    ref_end   = csdi_cfg.get("reference_end",   "2014-12-31")

    time_slices: Dict[str, Tuple[str, str]] = cfg.get("time_slices", {})
    experiments: List[str] = cfg.get("experiments", {}).get("select", ["historical"])

    # Paths
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / varname
    OUTPUT_DIR = ROOT / "data" / "outputs" / "csdi"
    THR_DIR    = OUTPUT_DIR / "_thresholds"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THR_DIR.mkdir(parents=True, exist_ok=True)

    # Inventory files by experiment
    exp_files: Dict[str, List[Path]] = {
        exp: sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / f"**/{exp}/*.nc"), recursive=True))
        for exp in experiments
    }

    # ---------- 1) Build/cache TN10p per model from historical ----------
    hist_files = exp_files.get("historical", [])
    hist_models = _models_in_files(hist_files, "historical")
    print(f"\n📦 Caching TN10p (baseline {ref_start}–{ref_end}) for {len(hist_models)} models…")

    for model in hist_models:
        cache_path = THR_DIR / f"{model}_tn10p_{ref_start[:4]}-{ref_end[:4]}.nc"
        if cache_path.exists():
            continue
        try:
            mfiles = _files_for_model_exp(DATA_DIR, model, "historical")
            # Prefilter to speed IO; still slice later
            mfiles = [p for p in mfiles if (y := _year_from_name(p)) and (1980 <= y <= 2014)] or mfiles
            if not mfiles:
                continue

            dsh = _open_mf(mfiles)
            dsh = _select_bbox(dsh, lat_bounds, lon_bounds)
            dsh = _slice_time_cf(dsh, ref_start, ref_end)
            if varname not in dsh or dsh.sizes.get("time", 0) == 0:
                dsh.close()
                continue

            tn_hist_c = convert_units_to(dsh[varname], "degC")
            thr_doy = _calendar10p_threshold(tn_hist_c).astype("float32")

            xr.Dataset({"tn10p": thr_doy}).to_netcdf(cache_path, engine="h5netcdf")
            dsh.close()
            print(f"   ✓ cached {model}")
        except Exception as e:
            print(f"   ⚠️ {model}: {e}")

    thr_cache = {p.name.split("_tn10p_")[0]: p for p in THR_DIR.glob("*_tn10p_*.nc")}

    # ---------- 2) Per experiment & time-slice, streaming ensemble ----------
    for experiment, files in exp_files.items():
        print(f"\n📁 Experiment: {experiment}  ({len(files)} files)")
        if not files:
            continue

        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.startswith("Baseline")]

        models = _models_in_files(files, experiment)
        print(f"   Models detected: {len(models)}")

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            sY, eY = int(start[:4]), int(end[:4])
            print(f"\n→ Time slice: {slice_name}  ({start} → {end})")

            ens_sum = None
            ens_n = 0
            used_models: List[str] = []

            for model in models:
                try:
                    scen_files = _files_for_model_exp(DATA_DIR, model, experiment)
                    scen_files = [p for p in scen_files if (y := _year_from_name(p)) and (sY <= y <= eY)]
                    if not scen_files:
                        continue

                    ds = _open_mf(scen_files)
                    ds = _select_bbox(ds, lat_bounds, lon_bounds)
                    ds = _slice_time_cf(ds, start, end)
                    if varname not in ds or ds.sizes.get("time", 0) == 0:
                        ds.close()
                        continue

                    tn_scen_c = convert_units_to(ds[varname], "degC")

                    cache_path = thr_cache.get(model)
                    if cache_path is None:
                        ds.close()
                        continue
                    thr_ds = xr.open_dataset(cache_path, engine="h5netcdf")
                    tn10p = thr_ds["tn10p"]

                    # Align threshold grid if needed
                    if ("lat" in tn10p.coords and "lat" in tn_scen_c.coords
                        and ("lon" in tn10p.coords and "lon" in tn_scen_c.coords)):
                        if (tn10p.sizes.get("lat") != tn_scen_c.sizes.get("lat")
                            or tn10p.sizes.get("lon") != tn_scen_c.sizes.get("lon")):
                            tn10p = tn10p.reindex(lat=tn_scen_c["lat"], lon=tn_scen_c["lon"], method=None)

                    scen_doy = tn_scen_c["time"].dt.dayofyear
                    max_doy = int(scen_doy.max())
                    tn10p = _ensure_thr_has_day(tn10p, max_doy)
                    thr_t = tn10p.sel(dayofyear=scen_doy)

                    cold = tn_scen_c < thr_t
                    cold = cold.chunk({"time": -1})  # silence parallelized core-dim warnings

                    # Count cold-spell days per year
                    per_year = []
                    for yr, grp in cold.groupby("time.year"):
                        grp = grp.chunk({"time": -1})
                        days = _csdi_days_per_year(grp, runlen=runlen).assign_coords(year=yr)
                        per_year.append(days)
                    if not per_year:
                        ds.close(); thr_ds.close()
                        continue

                    csdi_yearly = xr.concat(per_year, dim="year")
                    csdi_mean = csdi_yearly.mean(dim="year").astype("float32").load()

                    # Streaming ensemble mean
                    if ens_sum is None:
                        ens_sum = csdi_mean.copy()
                        ens_n = 1
                    else:
                        if set(csdi_mean.dims) != set(ens_sum.dims) or any(
                            csdi_mean.sizes.get(d) != ens_sum.sizes.get(d) for d in csdi_mean.dims
                        ):
                            csdi_mean = csdi_mean.reindex_like(ens_sum)
                        ens_sum = ens_sum + csdi_mean
                        ens_n += 1

                    used_models.append(model)
                    ds.close(); thr_ds.close()

                except Exception as e:
                    print(f"   ⚠️ {model}: {e}")

            if ens_sum is None or ens_n == 0:
                print(f"   ❌ No valid CSDI outputs for {experiment} / {slice_name}.")
                continue

            ensemble_mean = (ens_sum / float(ens_n)).astype("float32")
            label = _label_slug(slice_name)
            out_nc = (OUTPUT_DIR / f"csdi_ensemble_mean_{experiment}_{label}.nc")

            xr.Dataset(
                {"csdi": ensemble_mean.assign_attrs({
                    "units": "days",
                    "long_name": f"CSDI (days in spells ≥ {runlen} with TN < model TN10p, baseline {ref_start}–{ref_end})",
                    "min_spell_length": runlen,
                    "threshold_reference": f"{ref_start} to {ref_end}",
                })},
                attrs={
                    "title": f"CSDI Ensemble Mean — {experiment} — {slice_name}",
                    "models_included": ", ".join(sorted(set(used_models))),
                    "created_by": "csdi_compute.py",
                },
            ).to_netcdf(out_nc, engine="h5netcdf")
            print(f"   ✅ Saved → {out_nc.name}  (models: {len(used_models)})")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")