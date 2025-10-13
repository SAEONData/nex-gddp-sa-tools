#!/usr/bin/env python3
"""
spi_compute.py — WMO/McKee style SPI
---------------------------------------------------------------
Computes SPI-3/6/12 using a mixed distribution:
- P(X = 0) = q  (zero-precip months)
- P(X > 0) ~ Gamma(alpha, beta)  (shape, scale)
CDF: H(x) = q + (1 - q) * G_gamma(x; alpha, beta)
SPI = Phi^{-1}(H), where Phi^{-1} is the inverse standard normal CDF.

Key robustness choices:
- Read *combined* files: data/combined/pr/<experiment>/pr_<MODEL>_<experiment>.nc
- Convert monthly timestamps to NumPy datetime64[ns] "YYYY-MM-01"
  so we can concat safely across mixed CF calendars.
- Slice by YEAR using NumPy datetimes.
- Fit parameters per MODEL, per SCALE k, per CALENDAR MONTH (1..12) on the
  historical baseline, then reuse for all slices.

Outputs (per k in {3,6,12}):
  data/outputs/spi/spi{k}_ensemble_mean_<experiment>_<slice>.nc

Additionally writes a generic baseline file for default scale (k=6 by default):
  data/outputs/spi/spi_ensemble_mean_historical_baseline_1995-2014.nc
"""

from __future__ import annotations
from pathlib import Path
from collections import OrderedDict, defaultdict
import glob, time, warnings, os
import numpy as np
import xarray as xr
import dask
from dask.diagnostics import ProgressBar
from xclim.core.units import convert_units_to

# ── try canonical tools; fall back gracefully ──
_HAS_SCIPY = True
try:
    from scipy.special import gammainc  # regularized lower incomplete gamma P(a, x)
    try:
        from scipy.stats import norm as _scipy_norm
        _has_scipy_norm = True
    except Exception:
        _has_scipy_norm = False
except Exception:
    _HAS_SCIPY = False
    _has_scipy_norm = False

# ── runtime guards ──
warnings.filterwarnings("ignore", message=".*already exists and will be overwritten.")
warnings.filterwarnings("ignore", message=".*Converting non-ns precision datetimes.*")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("XR_USE_FLOX", "0")  # safer groupby backend

# ── helpers ──
def _label_slug(s: str) -> str:
    return (
        s.lower()
         .replace(" ", "_")
         .replace("(", "").replace(")", "")
         .replace("–", "-").replace("—", "-")
         .replace("/", "-")
    )

def _select_bbox(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset:
    if "lat" not in ds.coords or "lon" not in ds.coords:
        raise ValueError("Dataset is missing 'lat' and/or 'lon'.")
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1]); lon_asc = bool(lon[0] < lon[-1])
    return ds.sel(
        lat=slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0]),
        lon=slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0]),
    )

def _group_files_by_model(files, experiment):
    """Return OrderedDict{model: [sorted filepaths]} from combined layout."""
    groups = {}
    for f in files:
        p = Path(f)
        parts = p.parts
        if experiment in parts:
            idx = parts.index(experiment)
            model = parts[idx - 1] if idx > 0 else p.stem
        else:
            stem = p.stem.split("_")
            model = stem[1] if len(stem) > 1 else p.stem
        groups.setdefault(model, []).append(str(p))
    return OrderedDict((m, sorted(fs)) for m, fs in sorted(groups.items()))

def _decode_cftime(ds: xr.Dataset) -> xr.Dataset:
    """Decode CF times to cftime objects if needed; no-op if already decoded."""
    try:
        return xr.decode_cf(ds, use_cftime=True)
    except Exception:
        return ds

def _month_to_np64(da: xr.DataArray) -> xr.DataArray:
    """Normalize monthly coord to NumPy 'YYYY-MM-01' for safe concat across calendars."""
    years  = da["time"].dt.year.astype("int32").values
    months = da["time"].dt.month.astype("int8").values
    ts = np.array([np.datetime64(f"{int(y):04d}-{int(m):02d}-01") for y, m in zip(years, months)])
    return da.assign_coords(time=("time", ts))

def _file_to_monthly(pr_file, lat_bounds, lon_bounds) -> xr.DataArray:
    """
    Open 1 file, subset bbox, convert to mm/day, resample to monthly totals (lazy),
    normalize month coordinates to numpy monthly timestamps, and return the series.
    """
    ds = xr.open_dataset(
        pr_file,
        engine="h5netcdf",           # avoid netcdf4 segfaults
        chunks={"time": -1},         # one time chunk
        decode_times=False,          # decode explicitly (cftime)
        mask_and_scale=True,
        lock=False,
    )
    ds = _decode_cftime(ds)
    ds = _select_bbox(ds, lat_bounds, lon_bounds)

    if "pr" not in ds:
        raise ValueError("Missing 'pr' variable in dataset.")
    pr_day = convert_units_to(ds["pr"], "mm/day")

    # Monthly totals: first-of-month index in the file's native calendar
    pr_mon = pr_day.resample(time="MS").sum(dim="time")
    pr_mon = pr_mon.rename("pr_monthly_mm").astype("float32")
    pr_mon.attrs["units"] = "mm/month"

    # Normalize to numpy monthly timestamps for safe concat across calendars
    pr_mon = _month_to_np64(pr_mon)
    return pr_mon

def _model_monthly_series(model_files, lat_bounds, lon_bounds) -> xr.DataArray | None:
    """Monthly totals per file, then concat along time with normalized NumPy months."""
    monthly_list = []
    for f in model_files:
        try:
            monthly_list.append(_file_to_monthly(f, lat_bounds, lon_bounds))
        except Exception as e:
            print(f"   ⚠️ Skipped {Path(f).name}: {e}")
    if not monthly_list:
        return None
    series = xr.concat(monthly_list, dim="time").sortby("time")
    return series

def _rolling_total(pr_mon: xr.DataArray, k: int) -> xr.DataArray:
    return pr_mon.rolling(time=k, min_periods=k).sum().rename(f"P{k}")

def _sel_years(da: xr.DataArray, start, end) -> xr.DataArray:
    """Select by YEAR range using numpy monthly timestamps."""
    y0 = int(str(start)[:4]); y1 = int(str(end)[:4])
    s = np.datetime64(f"{y0:04d}-01-01")
    e = np.datetime64(f"{y1:04d}-12-31")
    return da.sel(time=slice(s, e))

# ── Inverse normal ppf (fallback if SciPy missing) ──
def _acklam_ppf(p: np.ndarray) -> np.ndarray:
    # Peter John Acklam's approximation for inverse normal CDF.
    # Vectorized for numpy arrays. Domain (0,1). Clips to avoid infs.
    p = np.asarray(p, dtype=np.float64)
    p = np.clip(p, 1e-12, 1 - 1e-12)
    # Coeffs
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    pl = 0.02425
    pu = 1 - pl
    x = np.empty_like(p)
    # Lower region
    m = p < pl
    if np.any(m):
        q = np.sqrt(-2*np.log(p[m]))
        x[m] = (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    # Upper region
    m = p > pu
    if np.any(m):
        q = np.sqrt(-2*np.log(1 - p[m]))
        x[m] = -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                  ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    # Central region
    m = (p >= pl) & (p <= pu)
    if np.any(m):
        q = p[m] - 0.5
        r = q*q
        x[m] = (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
                (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)
    return x.astype(np.float64)

def _ppf_xr(p_da: xr.DataArray) -> xr.DataArray:
    if _has_scipy_norm:
        return xr.apply_ufunc(
            _scipy_norm.ppf, p_da,
            dask="parallelized",
            output_dtypes=[np.float64],
        )
    # fallback: Acklam
    return xr.apply_ufunc(
        _acklam_ppf, p_da,
        dask="parallelized",
        output_dtypes=[np.float64],
    )

# ── Gamma fit and CDF (mixed with zeros) ──
def _fit_gamma_mixed_by_month(Pk_ref: xr.DataArray) -> xr.Dataset:
    """
    Fit q (zero prob), alpha (shape), beta (scale) for each calendar month,
    per grid cell, from baseline rolling totals Pk_ref.

    Returns a Dataset with dims: month (1..12), lat, lon.
    """
    # counts
    bym = Pk_ref.groupby("time.month")
    n_all  = bym.count(dim="time")                      # total months
    x_pos  = Pk_ref.where(Pk_ref > 0)
    n_pos  = x_pos.groupby("time.month").count(dim="time")  # positive-only count

    # moments for positive values
    sum_x  = x_pos.groupby("time.month").sum(dim="time")
    sum_x2 = (x_pos**2).groupby("time.month").sum(dim="time")

    mean_pos = sum_x / n_pos
    var_pos  = (sum_x2 / n_pos) - mean_pos**2

    # guard small samples / zero variance
    valid = (n_pos > 1) & (var_pos > 0) & np.isfinite(var_pos)

    # gamma parameters (method-of-moments)
    alpha = (mean_pos**2 / var_pos).where(valid)
    beta  = (var_pos / mean_pos).where(valid)

    # zero probability
    q = (1 - (n_pos / n_all)).where(n_all > 0)

    ds = xr.Dataset(
        {
            "q": q.astype("float64"),
            "alpha": alpha.astype("float64"),
            "beta": beta.astype("float64"),
        }
    )
    ds["month"] = np.arange(1, 13, dtype=np.int16)
    return ds

def _gamma_cdf_xr(x: xr.DataArray, alpha: xr.DataArray, beta: xr.DataArray) -> xr.DataArray:
    """
    Regularized lower gamma CDF G(a, x/b) using SciPy if available.
    """
    if not _HAS_SCIPY:
        # No SciPy: we'll return NaNs here; caller will fall back to z-score SPI.
        return xr.full_like(x, np.nan, dtype="float64")

    def _cdf_np(x_, a_, b_):
        # Handle non-positive or invalid params safely
        x_ = np.asarray(x_, dtype=np.float64)
        a_ = np.asarray(a_, dtype=np.float64)
        b_ = np.asarray(b_, dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = x_ / b_
        out = gammainc(a_, z)  # already regularized
        # Where params invalid (nan/inf/<=0), set nan
        mask_bad = ~np.isfinite(out) | (a_ <= 0) | (b_ <= 0)
        out = np.where(mask_bad, np.nan, out)
        return out

    return xr.apply_ufunc(
        _cdf_np, x, alpha, beta,
        dask="parallelized",
        output_dtypes=[np.float64],
    )

def _spi_gamma_mixed(Pk: xr.DataArray, params: xr.Dataset) -> xr.DataArray:
    """
    SPI from mixed distribution: H = q + (1-q)*G_gamma; SPI = Phi^{-1}(H)
    Params are per-month; select by Pk.time.dt.month.
    """
    month = Pk["time"].dt.month
    q_m     = params["q"].sel(month=month)
    alpha_m = params["alpha"].sel(month=month)
    beta_m  = params["beta"].sel(month=month)

    G = _gamma_cdf_xr(Pk, alpha_m, beta_m)  # may be NaN if SciPy missing or params invalid
    H = q_m + (1.0 - q_m) * G
    H = H.clip(1e-12, 1 - 1e-12)
    spi = _ppf_xr(H)
    return spi.astype("float32")

def _spi_zscore_per_month(Pk: xr.DataArray, ref_start, ref_end) -> xr.DataArray:
    """
    Fallback SPI (no SciPy): z-score per calendar month (still WMO-consistent in spirit).
    """
    ref = _sel_years(Pk, ref_start, ref_end)
    mu  = ref.groupby("time.month").mean(dim="time")
    sig = ref.groupby("time.month").std(dim="time")
    month = Pk["time"].dt.month
    mu_t  = mu.sel(month=month)
    sig_t = sig.sel(month=month)
    sig_t = xr.where(sig_t < 1e-12, np.nan, sig_t)
    spi = (Pk - mu_t) / sig_t
    return spi.astype("float32")

# ── main ──
def run(cfg):
    t0 = time.time()
    print("🧮 Starting SPI (WMO/McKee) processing…")
    if not _HAS_SCIPY:
        print("   ⚠️ SciPy not found: falling back to per-month z-score SPI (no Gamma CDF).")

    # --- config ---
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

    spi_cfg        = cfg.get("spi", {})
    scales         = list(spi_cfg.get("scales", [3, 6, 12]))  # months
    default_scale  = int(spi_cfg.get("default_scale", 6))     # used for generic 'spi_' baseline
    ref_start      = spi_cfg.get("reference_start", "1995-01-01")
    ref_end        = spi_cfg.get("reference_end",   "2014-12-31")

    time_slices = cfg.get("time_slices", {})
    experiments = cfg.get("experiments", {}).get("select", ["historical"])

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "combined" / "pr"
    OUTPUT_DIR = ROOT / "data" / "outputs" / "spi"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- A) Per-model baseline parameter fit (historical) ---
    hist_files = sorted(glob.glob(str(DATA_DIR / "**/historical/*.nc"), recursive=True))
    if not hist_files:
        print("❌ No historical files found; SPI cannot be calibrated.")
        return
    groups_hist = _group_files_by_model(hist_files, "historical")

    # For each model: monthly series, rolling totals, fit params per month
    fitted_params: dict[str, dict[int, xr.Dataset]] = {}
    print(f"\n📊 Fitting baseline params ({int(str(ref_start)[:4])}–{int(str(ref_end)[:4])}) per model & scale…")
    for model, files in groups_hist.items():
        series = _model_monthly_series(files, lat_bounds, lon_bounds)
        if series is None or series.time.size == 0:
            print(f"   ⚠️ {model}: no monthly data; skipping.")
            continue
        fitted_params[model] = {}
        for k in scales:
            Pk = _rolling_total(series, k)
            Pk_ref = _sel_years(Pk, ref_start, ref_end)
            if Pk_ref.time.size == 0:
                continue
            if _HAS_SCIPY:
                params = _fit_gamma_mixed_by_month(Pk_ref)
            else:
                # store empty; will fall back to z-score directly
                params = xr.Dataset()
            fitted_params[model][k] = params
        print(f"   ✓ {model}: baseline fitted for k={scales}")

    if not fitted_params:
        print("❌ No baseline parameters computed; aborting.")
        return

    # --- B) SPI per experiment/slice; ensemble mean; write outputs ---
    model_file_counts = defaultdict(int)

    for experiment in experiments:
        print(f"\n📁 Experiment: {experiment}")
        exp_files = sorted(glob.glob(str(DATA_DIR / f"**/{experiment}/*.nc"), recursive=True))
        if not exp_files:
            print("   ⚠️ No files found.")
            continue
        groups_exp = _group_files_by_model(exp_files, experiment)

        slice_names = ["Baseline (1995–2014)"] if experiment == "historical" else \
                      [n for n in time_slices if not n.lower().startswith("baseline")]

        for slice_name in slice_names:
            start, end = time_slices.get(slice_name, [None, None])
            if not start or not end:
                continue
            print(f"\n→ Time slice: {slice_name}  ({start} to {end})")

            per_scale_model_arrays: dict[int, list[xr.DataArray]] = {k: [] for k in scales}
            per_scale_model_names:  dict[int, list[str]]           = {k: [] for k in scales}

            for model, files in groups_exp.items():
                if model not in fitted_params:
                    continue
                series = _model_monthly_series(files, lat_bounds, lon_bounds)
                if series is None or series.time.size == 0:
                    continue

                for k in scales:
                    Pk = _rolling_total(series, k)
                    Pk_slice = _sel_years(Pk, start, end)
                    if Pk_slice.time.size == 0:
                        continue

                    if _HAS_SCIPY and k in fitted_params[model] and "q" in fitted_params[model][k]:
                        spi_ts = _spi_gamma_mixed(Pk_slice, fitted_params[model][k])
                    else:
                        # fallback: per-month z-score against baseline
                        spi_ts = _spi_zscore_per_month(xr.concat([_sel_years(Pk, ref_start, ref_end), Pk_slice], dim="time"), ref_start, ref_end)
                        # keep only slice months
                        spi_ts = _sel_years(spi_ts, start, end)

                    # mean over slice months
                    spi_mean = spi_ts.mean(dim="time").astype("float32")
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
                    "units": "",  # unitless (z-score)
                    "long_name": f"Standardized Precipitation Index (SPI-{k}), mean over slice",
                    "reference_period": f"{int(str(ref_start)[:4])}–{int(str(ref_end)[:4])}",
                    "scale_months": k,
                    "method": "WMO/McKee mixed distribution (Gamma+zeros) → normal" if _HAS_SCIPY else "per-month z-score (fallback)",
                })

                label = _label_slug(slice_name)
                out_nc = OUTPUT_DIR / f"{out_var}_ensemble_mean_{experiment}_{label}.nc"
                xr.Dataset({out_var: out_da}).to_netcdf(
                    out_nc,
                    engine="h5netcdf",
                    encoding={out_var: dict(zlib=True, complevel=4, shuffle=True, dtype="float32")}
                )
                print(f"   ✅ Saved → {out_nc.name}")

                # Also write the GENERIC baseline file the anomaly pipeline expects
                if (experiment == "historical"
                    and label == "baseline_1995-2014"
                    and k == default_scale):
                    generic_nc = OUTPUT_DIR / f"spi_ensemble_mean_{experiment}_{label}.nc"
                    xr.Dataset({"spi": out_da.rename("spi")}).to_netcdf(
                        generic_nc,
                        engine="h5netcdf",
                        encoding={"spi": dict(zlib=True, complevel=4, shuffle=True, dtype="float32")}
                    )
                    print(f"   🔁 Also wrote generic baseline for anomalies → {generic_nc.name}")

    # summary
    print("\n📊 File count per model (all experiments combined):")
    for m in sorted(model_file_counts):
        print(f"   {m:30} {model_file_counts[m]:>4d}")

    print(f"\n⏱️ Completed in {round(time.time() - t0, 1)} s.")