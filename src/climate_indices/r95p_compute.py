#!/usr/bin/env python3
"""
r95p_compute.py
---------------------------------------------------------------
Calculate CMIP6 R95p (total rainfall from very wet days in mm)
using xclim’s icclim.R95p indicator. Mirrors r95ptot_compute.py.
"""

from pathlib import Path
import glob, time, os
import warnings
import xarray as xr
import dask
from dask.diagnostics import ProgressBar
from xclim.core.indicator import registry

warnings.filterwarnings("ignore")
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

def open_dataset_safe(path: Path, chunks) -> xr.Dataset:
    try:
        return xr.open_dataset(path, chunks=chunks)
    except Exception:
        return xr.open_dataset(path, engine="netcdf4", chunks=chunks, lock=True)

def safe_subset(ds: xr.Dataset, lat_bounds, lon_bounds) -> xr.Dataset | None:
    lat0, lat1 = lat_bounds
    lon0, lon1 = lon_bounds
    lat_slice = slice(lat1, lat0) if ds.lat[0] > ds.lat[-1] else slice(lat0, lat1)

    if (ds.lon > 180).all():
        lon0 = (lon0 + 360) % 360
        lon1 = (lon1 + 360) % 360

    ds_sub = ds.sel(lat=lat_slice, lon=slice(lon0, lon1))

    if ds_sub.dims.get("lon", 0) == 0:
        lon_vals = ds.lon
        mask = (lon_vals >= lon0) & (lon_vals <= lon1)
        ds_sub = ds.sel(lat=lat_slice).where(mask, drop=True)

    if ds_sub.dims.get("lat", 0) == 0 or ds_sub.dims.get("lon", 0) == 0:
        return None

    return ds_sub

def run(cfg):
    t0 = time.time()
    print("Starting R95p processing …\n")

    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    rcfg = cfg["r95p"]

    thresh_mm = rcfg.get("threshold_mm", 1.0)
    aggr_code = rcfg.get("aggregation_code", "YS")
    aggr_label = rcfg.get("aggregation", "annual")
    ref_start = rcfg.get("reference_start", "1981-01-01")
    ref_end = rcfg.get("reference_end", "2010-12-31")

    ROOT = Path(__file__).resolve().parents[2]
    DATA_DIR = ROOT / "data" / "pr"
    OUT_DIR = ROOT / "data" / "outputs" / "r95p"
    EXPER = cfg.get("experiments", {}).get("select", ["historical"])
    MIN_BYTES = 500_000

    R95_cls = registry.get("icclim.R95p")
    if R95_cls is None:
        raise RuntimeError("'icclim.R95p' not registered.")
    R95 = R95_cls()
    chunks = {"time": -1}

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

                if ds0 is None or "pr" not in ds0:
                    raise ValueError("Invalid or missing 'pr' variable")

                pr = ds0["pr"] * 86400.0
                pr.attrs["units"] = "mm/day"

                ref = pr.sel(time=slice(ref_start, ref_end))
                wet = ref.where(ref > thresh_mm)

                if wet.count().compute() < 10:
                    raise ValueError("Too few wet days for percentile calc")

                pr_per = wet.quantile(0.95, dim="time").compute()
                pr_per.attrs["units"] = "mm/day"

                r95p = R95(pr=pr, pr_per=pr_per, freq=aggr_code).compute()

                if r95p.isnull().all():
                    raise ValueError("All values are NaN")

                model_data.append(r95p.mean("time"))
                try:
                    model_names.append(f.parts[f.parts.index(exp) - 1])
                except ValueError:
                    model_names.append(f.stem.split("_")[2])

            except Exception as exc:
                print(f"   ⚠️  {f.name}: {exc}")

        if not model_data:
            print("   ❌  No valid outputs for this experiment")
            continue

        with ProgressBar():
            stacked = xr.concat(dask.compute(*model_data), dim="model")
            stacked["model"] = model_names
            ens_mean = stacked.mean("model")

        out_nc = OUT_DIR / f"r95p_ensemble_mean_{exp}.nc"
        out_nc.parent.mkdir(parents=True, exist_ok=True)

        attrs = dict(
            title=f"R95p Ensemble Mean – {exp}",
            description=(f"Total rainfall (mm) on days above 95th percentile of wet days "
                         f"(threshold > {thresh_mm} mm)\n"
                         f"Reference: {ref_start} to {ref_end}, Aggregation: {aggr_label}"),
            units="mm",
            created_by="r95p_compute.py",
            models_included=", ".join(sorted(set(model_names))),
        )

        xr.Dataset({"r95p": ens_mean}, attrs=attrs).to_netcdf(out_nc)
        print(f"   ✅ Saved → {out_nc}")

    print(f"\n✔︎ Done in {time.time() - t0:.1f}s")