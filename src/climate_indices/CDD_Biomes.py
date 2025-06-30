#!/usr/bin/env python3
"""
cdd_biomes.py
---------------------------------------------------------------
Calculate and plot CMIP6 maximum consecutive dry days (CDD)
aggregated by South African vegetation biomes, with configurable
thresholds and aggregation (annual, monthly, seasonal).

To be executed via `run_climate_indices.py`, using config from
`climate_indices_config.yml`.
"""

from pathlib import Path
import sys, glob
import numpy as np
import pandas as pd
import xarray as xr
import geopandas as gpd
import regionmask
import matplotlib.pyplot as plt
import yaml

# 1. Max CDD helper
def max_cdd(precip: xr.DataArray, thr: float = 1.0) -> xr.DataArray:
    dry = (precip < thr).astype(int)

    def run_len(arr):
        pad  = np.concatenate(([0], arr, [0]))
        diff = np.diff(pad)
        starts = np.where(diff == 1)[0]
        ends   = np.where(diff == -1)[0]
        return (ends - starts).max() if starts.size else 0

    return xr.apply_ufunc(
        np.vectorize(run_len, signature="(t)->()"),
        dry,
        input_core_dims=[["time"]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[int],
    )

# 2. Main entry point
def run(cfg):
    # ── Load parameters ──────────────────────────────────────────
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    threshold  = cfg["cdd"].get("threshold_mm", 1.0)
    aggr       = cfg["cdd"].get("aggregation", "annual")

    # ── Paths ────────────────────────────────────────────────────
    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    SHAPEFILE  = ROOT / "climate_regions" / "cleaned_clim_reg_2025_06_30.shp"
    TOWNS_CSV  = ROOT / "cities" / "cities.csv"

    # ── Read shapefile and prepare region mask ───────────────────
    bioregions = gpd.read_file(SHAPEFILE).to_crs("EPSG:4326")
    region_mask = regionmask.Regions(
        outlines=bioregions.geometry,
        names=bioregions["Veg_Biome"],
        abbrevs=bioregions["Veg_Biome"],
        name="Bioregions",
    )

    # ── Collect NetCDF files ─────────────────────────────────────
    nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / "**/historical/*.nc"), recursive=True))
    print(f"Found {len(nc_files)} historical NetCDF files.")

    model_data = []
    model_names = []

    # ── Process each NetCDF file ─────────────────────────────────
    for nc_file in nc_files:
        try:
            ds = xr.open_dataset(nc_file)
            ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))
            pr = ds["pr"] * 86400.0  # kg/m²/s → mm/day

            # Aggregation
            if aggr == "monthly":
                grouped = pr.resample(time="MS")
            elif aggr == "seasonal":
                grouped = pr.resample(time="QS-DEC")
            else:
                grouped = pr.resample(time="YE")

            cdd = grouped.map(lambda d: max_cdd(d, thr=threshold))
            cdd_mean = cdd.mean(dim="time")

            # Region aggregation
            mask_da = region_mask.mask(cdd_mean)
            reg_cdd = cdd_mean.groupby(mask_da).mean()
            model_data.append(reg_cdd)

            # Model name extraction
            try:
                idx = nc_file.parts.index("historical")
                model_names.append(nc_file.parts[idx - 1])
            except ValueError:
                model_names.append(nc_file.stem.split("_")[2])

            print(f"Processed: {nc_file.name}")

        except Exception as exc:
            print(f"Error processing {nc_file.name}: {exc}")

    # ── Ensemble Mean ────────────────────────────────────────────
    if not model_data:
        raise RuntimeError("No datasets processed successfully.")

    stack = xr.concat(model_data, dim="model")
    stack["model"] = model_names
    ensemble_mean = stack.mean(dim="model")

    # ── Print results ────────────────────────────────────────────
    df = pd.DataFrame({
        "Bioregion": region_mask.names,
        "Max_CDD_days": ensemble_mean.values,
    }).sort_values("Max_CDD_days", ascending=False)

    print("\n Max Consecutive Dry Days (CDD) – Ensemble Mean")
    print(df.to_string(index=False))

    # ── Plotting ─────────────────────────────────────────────────
    bioregion_mean_df = pd.DataFrame({
        "Veg_Biome": region_mask.names,
        "Max_CDD_days": ensemble_mean.values,
    })
    merged = bioregions.merge(bioregion_mean_df, on="Veg_Biome")

    # Cities overlay
    towns_df = pd.read_csv(TOWNS_CSV, sep=";").rename(columns=str.strip)
    towns_gdf = gpd.GeoDataFrame(
        towns_df,
        geometry=gpd.points_from_xy(towns_df["lng"], towns_df["lat"]),
        crs="EPSG:4326",
    )

    fig, ax = plt.subplots(figsize=(12, 9))
    vmin, vmax = 0, merged["Max_CDD_days"].max()
    ticks = np.arange(0, vmax + 10, 10)

    merged.plot(
        column="Max_CDD_days",
        cmap="YlOrBr",
        linewidth=0.6,
        edgecolor="gray",
        legend=True,
        legend_kwds={
            "label": "Max Consecutive Dry Days (CDD)",
            "orientation": "vertical",
            "shrink": 0.7,
            "ticks": ticks,
        },
        ax=ax,
    )

    towns_gdf.plot(ax=ax, color="red", markersize=35, zorder=5)
    for x, y, label in zip(towns_gdf.geometry.x, towns_gdf.geometry.y, towns_gdf["city"]):
        ax.text(x + 0.1, y + 0.1, label, fontsize=8, ha="left", va="bottom")

    ax.set_title(
        f"CMIP6 Max Consecutive Dry Days (CDD) by Vegetation Biome\nAggregation: {aggr.capitalize()} | Threshold: {threshold} mm/day",
        fontsize=14
    )
    ax.set_axis_off()
    plt.tight_layout()
    plt.show()