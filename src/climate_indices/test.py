#!/usr/bin/env python3
"""
WSDI (Warm Spell Duration Index): Days per year in spells of tasmax > 90th percentile (1961–1990, 5-day window)
"""

import glob
from pathlib import Path
import xarray as xr
import numpy as np

np.seterr(invalid='ignore')  # Suppress warnings for NaNs during percentile calculation
# ───── Config ─────
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
tasmax_files = sorted(glob.glob(str(DATA_DIR / "tasmax" / "*" / "historical" / "*.nc")))
lat_bounds = [-35, -22]
lon_bounds = [16, 33]
base_period = slice("1961-01-01", "1990-12-31")
window = 5
min_spell_length = 6

print(f"📂 Found {len(tasmax_files)} historical tasmax NetCDF files.")

# ───── Helper: Compute Day-of-Year Percentiles ─────
def compute_tx90(tas: xr.DataArray) -> xr.DataArray:
    # Get base period
    tas_base = tas.sel(time=base_period)
    if tas_base.time.size == 0:
        raise ValueError("Base period data is empty — skipping file.")
        
    doy = tas_base.time.dt.dayofyear
    doy = xr.where(doy == 366, 365, doy)
    tas_base.coords["doy"] = doy
    
    # Rolling window percentile
    padded = tas_base.pad(time=(window//2, window//2), mode="reflect")
    rolling = padded.rolling(time=window, center=True).construct("window_dim")
    tx90 = rolling.reduce(np.nanpercentile, q=90, dim=["window_dim"])
    
    tx90.coords["doy"] = tx90.time.dt.dayofyear
    tx90 = tx90.groupby("doy").mean("time")
    return tx90

# ───── Helper: Warm Spell Detection ─────
def detect_warm_spells(warm_days: xr.DataArray, min_length: int) -> xr.DataArray:
    def spell_mask(arr):
        out = np.zeros_like(arr, dtype=int)
        count = 0
        for i in range(len(arr)):
            if arr[i]:
                count += 1
            else:
                if count >= min_length:
                    out[i - count:i] = 1
                count = 0
        if count >= min_length:
            out[-count:] = 1
        return out

    return xr.apply_ufunc(
        spell_mask,
        warm_days,
        input_core_dims=[["time"]],
        output_core_dims=[["time"]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[int],
    )

# ───── Process Each File ─────
model_wsdi = []
model_names = []

for file in tasmax_files:
    try:
        ds = xr.open_dataset(file)
        if "tasmax" not in ds:
            print(f"⚠️ Skipping {file}: 'tasmax' not found.")
            continue

        # Subset region and convert units
        tas = ds["tasmax"].sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))
        tas = tas - 273.15  # K → °C

        # Compute 90th percentile climatology
        tx90 = compute_tx90(tas)

        # Match percentile to full data via dayofyear
        doy = tas.time.dt.dayofyear
        doy = xr.where(doy == 366, 365, doy)
        tas.coords["doy"] = doy
        tx90_expanded = tx90.sel(doy=doy)

        # Identify warm days and warm spells
        warm_days = tas > tx90_expanded
        warm_spells = detect_warm_spells(warm_days, min_length=min_spell_length)

        # Compute annual WSDI (sum of warm spell days per year)
        wsdi = warm_spells.groupby("time.year").sum("time")
        wsdi_mean = wsdi.mean("year")

        model_wsdi.append(wsdi_mean)
        model_names.append(Path(file).parts[-3])
        print(f"✅ Processed: {Path(file).name}")

    except Exception as e:
        print(f"❌ Error processing {file}: {e}")

# ───── Ensemble Mean ─────
if model_wsdi:
    stack = xr.concat(model_wsdi, dim="model")
    stack["model"] = model_names
    ensemble_mean = stack.mean("model")

    print("\n📊 WSDI (Warm Spell Duration Index) — Ensemble Mean (days/year):")
    print(ensemble_mean)
else:
    print("❌ No WSDI data available for ensemble mean.")