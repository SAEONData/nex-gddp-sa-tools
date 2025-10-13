#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Combine yearly NetCDFs per model+scenario into one continuous file
for multiple variable families (e.g. pr, tasmax).

Hardcoded layout:
  <root>/<variable>/<model>/<scenario>/*.nc
Writes to:
  <root>/combined/<variable>/<scenario>/<variable>_<model>_<scenario>.nc
"""

import os
from pathlib import Path
from collections import defaultdict
import xarray as xr
import numpy as np
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

# ------------------- USER CONFIG ------------------- #
ROOT = Path("/Users/private/SAEON/SAEON_GitHub/nex-gddp-sa-tools/data").resolve()
VARIABLES = ["pr", "tasmax", "tasmin"]  # add more as needed
EXPERIMENTS = ["historical", "ssp126", "ssp245", "ssp370", "ssp585"]
OUT_DIR = ROOT / "combined"
LAT_BOUNDS = (-35, -22)
LON_BOUNDS = (16, 33)
COMPRESSION_LEVEL = 4
# --------------------------------------------------- #


def _normalize_latlon(ds: xr.Dataset) -> xr.Dataset:
    """Rename latitude/longitude to lat/lon if needed."""
    ren = {}
    if "latitude" in ds.dims and "lat" not in ds.dims:
        ren["latitude"] = "lat"
    if "longitude" in ds.dims and "lon" not in ds.dims:
        ren["longitude"] = "lon"
    return ds.rename(ren) if ren else ds


def _select_bbox(ds: xr.Dataset, lat_bounds=None, lon_bounds=None) -> xr.Dataset:
    """Optional bbox, robust to ascending/descending coords."""
    if lat_bounds is None or lon_bounds is None:
        return ds
    if "lat" not in ds.coords or "lon" not in ds.coords:
        return ds
    lat = ds["lat"]; lon = ds["lon"]
    lat_asc = bool(lat[0] < lat[-1])
    lon_asc = bool(lon[0] < lon[-1])
    lat_slice = slice(lat_bounds[0], lat_bounds[1]) if lat_asc else slice(lat_bounds[1], lat_bounds[0])
    lon_slice = slice(lon_bounds[0], lon_bounds[1]) if lon_asc else slice(lon_bounds[1], lon_bounds[0])
    return ds.sel(lat=lat_slice, lon=lon_slice)


def _open_mfdataset(files):
    """
    Robust multi-file open:
    1) try by_coords with safer compat flags,
    2) try nested concat along time,
    3) fallback: open each file decode_times=False, normalize, concat, then decode CF.
    """
    # 1) by_coords attempts (safer merge flags)
    for attempt in [
        dict(engine="h5netcdf", decode_times=True,  chunks={"time": -1},
             data_vars="minimal", coords="minimal", compat="override"),
        dict(engine="scipy",     decode_times=True,
             data_vars="minimal", coords="minimal", compat="override"),
        dict(engine="h5netcdf", decode_times=False, chunks={"time": -1},
             data_vars="minimal", coords="minimal", compat="override"),
        dict(engine="scipy",    decode_times=False,
             data_vars="minimal", coords="minimal", compat="override"),
    ]:
        try:
            return xr.open_mfdataset(
                files, combine="by_coords", parallel=False, **attempt
            )
        except Exception:
            pass

    # 2) nested concat along time (for slightly differing coords/attrs)
    for attempt in [
        dict(engine="h5netcdf", decode_times=True,  chunks={"time": -1}),
        dict(engine="scipy",     decode_times=True),
        dict(engine="h5netcdf", decode_times=False, chunks={"time": -1}),
        dict(engine="scipy",    decode_times=False),
    ]:
        try:
            return xr.open_mfdataset(
                files, combine="nested", concat_dim="time",
                parallel=False, **attempt
            )
        except Exception:
            pass

    # 3) FINAL FALLBACK: per-file open, normalize, concat, then decode CF
    dsets = []
    last_err = None
    for f in files:
        try:
            ds = None
            for eng in ("h5netcdf", "scipy"):
                try:
                    ds = xr.open_dataset(f, engine=eng, decode_times=False)
                    break
                except Exception:
                    ds = None
            if ds is None:
                raise RuntimeError(f"could not open {f} with h5netcdf/scipy")

            # normalize lon/lat names early
            ren = {}
            if "latitude" in ds.dims and "lat" not in ds.dims:
                ren["latitude"] = "lat"
            if "longitude" in ds.dims and "lon" not in ds.dims:
                ren["longitude"] = "lon"
            if ren:
                ds = ds.rename(ren)

            dsets.append(ds)
        except Exception as e:
            last_err = e

    if not dsets:
        raise RuntimeError(f"Failed to open dataset: {files[0]}  (last error: {last_err})")

    try:
        ds = xr.concat(dsets, dim="time", data_vars="minimal", coords="minimal", compat="override")
        if "time" in ds.coords:
            ds = ds.sortby("time")
            _, idx = np.unique(ds["time"].values, return_index=True)
            if len(idx) != ds.sizes["time"]:
                ds = ds.isel(time=np.sort(idx))

        try:
            ds = xr.decode_cf(ds, use_cftime=True)
        except Exception:
            pass  # keep undecoded if decoding fails

        return ds
    finally:
        for d in dsets:
            try:
                d.close()
            except Exception:
                pass


def _clean_before_write(ds: xr.Dataset) -> xr.Dataset:
    """
    Drop/clean items that often trigger write errors like:
    'NetCDF: String match to name in use' or reserved '_NCProperties'.
    """
    # Drop any coord duplicated as data_var
    for coord_name in list(ds.coords):
        if coord_name in ds.data_vars:
            ds = ds.drop_vars(coord_name)

    # Drop common *_bnds vars that frequently collide
    for bad in ["time_bnds", "lat_bnds", "lon_bnds", "bnds", "bounds", "climatology_bounds"]:
        if bad in ds.variables:
            ds = ds.drop_vars(bad)

    # Remove reserved/problematic global attributes (e.g., _NCProperties)
    for k in list(ds.attrs.keys()):
        if k == "_NCProperties" or k.startswith("_NC"):
            ds.attrs.pop(k, None)

    # Also scrub variable-level reserved attrs just in case
    for v in ds.variables:
        for k in list(ds[v].attrs.keys()):
            if k == "_NCProperties" or k.startswith("_NC"):
                try:
                    del ds[v].attrs[k]
                except Exception:
                    pass

    # Ensure global attrs are safely stringifiable
    safe_attrs = {}
    for k, v in ds.attrs.items():
        try:
            _ = str(v)
            safe_attrs[k] = v
        except Exception:
            continue
    ds.attrs = safe_attrs

    # Clear encodings that sometimes carry conflicting hints
    try:
        for v in ds.data_vars:
            ds[v].encoding.clear()
    except Exception:
        pass

    return ds


def combine_group(var, model, scenario, files, out_dir):
    """Combine all yearly files for one variable+model+scenario."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{var}_{model}_{scenario}.nc"
    if out_path.exists():
        print(f"⏭️  Skipping existing {out_path.name}")
        return

    print(f"🔹 Combining {len(files)} files → {out_path.name}")

    ds = _open_mfdataset([str(f) for f in files])
    try:
        ds = _normalize_latlon(ds)
        ds = _select_bbox(ds, LAT_BOUNDS, LON_BOUNDS)

        # keep only target var
        if var not in ds.data_vars:
            raise ValueError(f"'{var}' not found in dataset.")
        ds = ds[[var]]

        # sort & deduplicate time
        if "time" in ds.coords:
            ds = ds.sortby("time")
            _, idx = np.unique(ds["time"], return_index=True)
            if len(idx) != ds.sizes["time"]:
                ds = ds.isel(time=np.sort(idx))

        # --- Clean problematic metadata before writing ---
        ds = _clean_before_write(ds)

        # compression
        comp = dict(zlib=True, complevel=COMPRESSION_LEVEL, shuffle=True)
        encoding = {v: comp for v in ds.data_vars}

        # Use h5netcdf engine for safer writes
        ds.to_netcdf(out_path, encoding=encoding, engine="h5netcdf", mode="w")
        print(f"✅ Saved: {out_path}")
    finally:
        try:
            ds.close()
        except Exception:
            pass


def main():
    for var in VARIABLES:
        var_dir = ROOT / var
        if not var_dir.exists():
            print(f"⚠️  Missing variable dir: {var_dir}")
            continue

        print(f"\n📂 Processing variable: {var}")

        for scenario in EXPERIMENTS:
            scenario_files = list(var_dir.glob(f"*/{scenario}/*.nc"))
            if not scenario_files:
                print(f"   ⚠️  No files for {scenario}")
                continue

            # group by model
            by_model = defaultdict(list)
            for f in scenario_files:
                model = f.parent.parent.name  # var/model/scenario/file.nc
                by_model[model].append(f)

            print(f"   Scenario {scenario}: {len(by_model)} models")

            for model, files in sorted(by_model.items()):
                try:
                    combine_group(var, model, scenario, sorted(files), OUT_DIR / var / scenario)
                except Exception as e:
                    print(f"❌ Failed {var}/{model}/{scenario}: {e}")


if __name__ == "__main__":
    main()