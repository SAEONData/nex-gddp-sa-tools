#!/usr/bin/env python3
"""
r95ptot_compute.py
---------------------------------------------------------------
Calculate CMIP6 R95pTOT (% rainfall from very wet days) using
xclim’s icclim.R95PTOT indicator. Mirrors cdd_compute.py style.
"""

from pathlib import Path
import glob, time, os
from collections import defaultdict
import warnings

import xarray as xr
from xclim.core.indicator import registry
from dask.diagnostics import ProgressBar
import dask

warnings.filterwarnings("ignore")
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"  # prevent HDF5 segmentation faults

# ─────────────────── helper: safely open dataset ─────────────────────
def open_dataset_safe(path: Path, chunks) -> xr.Dataset:
    try:
        return xr.open_dataset(path, chunks=chunks)
    except Exception:
        return xr.open_dataset(path, engine="netcdf4", chunks=chunks, lock=True)

# ─────────────────── helper: safe spatial subset ─────────────────────
def safe_subset(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset | None:
    lat0, lat1 = lat_bounds
    lon0, lon1 = lon_bounds
    lat_slice = slice(lat1, lat0) if ds.lat[0] > ds.lat[-1] else slice(lat0, lat1)

    if (ds.lon > 180).all():
        lon0 = (lon0 + 360) % 360
        lon1 = (lon1 + 360) % 360

    ds_sub = ds.sel(lat=lat_slice, lon=slice(lon0, lon1))

    if ds_sub.dims.get("lon", 0) == 0:  # fallback mask for wrapped grids
        lon_vals = ds.lon
        mask = (lon_vals >= lon0) & (lon_vals <= lon1)
        ds_sub = ds.sel(lat=lat_slice).where(mask, drop=True)

    if ds_sub.dims.get("lat", 0) == 0 or ds_sub.dims.get("lon", 0) == 0:
        return None

    return ds_sub

# ─────────────────────────────── main ────────────────────────────────
def run(cfg):
    t0 = time.time()
    print("Starting R95pTOT processing …\n")

    # ─── Configuration ───
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    rcfg       = cfg["r95ptot"]

    thresh_mm  = rcfg.get("threshold_mm", 1.0)
    aggr_code  = rcfg.get("aggregation_code", "YS")
    aggr_label = rcfg.get("aggregation", "annual")
    ref_start  = rcfg.get("reference_start", "1981-01-01")
    ref_end    = rcfg.get("reference_end",   "2010-12-31")

    ROOT      = Path(__file__).resolve().parents[2]
    DATA_DIR  = ROOT / "data" / "pr"
    OUT_DIR   = ROOT / "data" / "outputs" / "r95ptot"
    EXPER     = cfg.get("experiments", {}).get("select", ["historical"])
    MIN_BYTES = 500_000  # skip files smaller than ~500KB

    R95_cls = registry.get("icclim.R95PTOT")
    if R95_cls is None:
        raise RuntimeError("'icclim.R95PTOT' not registered in xclim.")
    R95 = R95_cls()
    chunks = {"time": -1}

    # ─── Process each experiment ───
    for exp in EXPER:
        print(f"\n▶︎ Experiment: {exp}")
        files = sorted(Path(p).resolve()
                       for p in glob.glob(str(DATA_DIR / f"**/{exp}/*.nc"), recursive=True))
        print(f"   {len(files)} files found")

        if not files:
            continue

        model_data, model_names = [], []

        for i, f in enumerate(files, 1):
            print(f" [{i}/{len(files)}] {f.name}")
            try:
                if f.stat().st_size < MIN_BYTES:
                    print("   skipped (file too small)")
                    continue

                ds0 = open_dataset_safe(f, chunks=chunks)
                ds0 = safe_subset(ds0, lat_bounds, lon_bounds)

                if ds0 is None:
                    print("   skipped (outside bounding box)")
                    continue

                if "pr" not in ds0:
                    raise ValueError("Missing 'pr' variable")

                pr = ds0["pr"] * 86400.0  # convert from kg/m²/s to mm/day
                pr.attrs["units"] = "mm/day"

                ref = pr.sel(time=slice(ref_start, ref_end))
                wet = ref.where(ref > thresh_mm)

                wet_count = wet.count().compute()
                if wet_count < 10:
                    raise ValueError(f"Too few wet days for threshold: {wet_count.values}")

                pr_per = wet.quantile(0.95, dim="time").compute()
                pr_per.attrs["units"] = "mm/day"

                r95 = R95(pr=pr, pr_per=pr_per, freq=aggr_code).compute()
                if r95.isnull().all():
                    raise ValueError("All values are NaN")

                model_data.append(r95.mean("time"))

                try:
                    model_names.append(f.parts[f.parts.index(exp) - 1])
                except ValueError:
                    model_names.append(f.stem.split("_")[2])

            except Exception as exc:
                print(f"   ⚠️  {f.name}: {exc}")

        # ─── Ensemble calculation ───
        if not model_data:
            print("   ❌  No valid outputs for this experiment")
            continue

        with ProgressBar():
            stacked = xr.concat(dask.compute(*model_data), dim="model")
            stacked["model"] = model_names
            ens_mean = stacked.mean("model")

        # ─── Output file ───
        out_nc = OUT_DIR / f"r95ptot_ensemble_mean_{exp}.nc"
        out_nc.parent.mkdir(parents=True, exist_ok=True)

        attrs = dict(
            title=f"R95pTOT Ensemble Mean – {exp}",
            description=(f"Fraction of total precipitation on >95th-pct wet days "
                         f"(wet-day > {thresh_mm} mm)\n"
                         f"Reference: {ref_start} to {ref_end}, Aggregation: {aggr_label}"),
            units="%",
            created_by="r95ptot_compute.py",
            models_included=", ".join(sorted(set(model_names))),
        )

        xr.Dataset({"r95ptot": ens_mean}, attrs=attrs).to_netcdf(out_nc)
        print(f"   ✅ Saved → {out_nc}")

    print(f"\n✔︎ Done in {time.time() - t0:.1f}s")