#!/usr/bin/env python3
"""
cdd_biomes.py
---------------------------------------------------------------
Calculate and plot CMIP6 maximum consecutive dry days (CDD)
aggregated by South African vegetation biomes, with configurable
thresholds and aggregation (annual, monthly, seasonal).
"""

from pathlib import Path
import glob, time
import numpy as np
import pandas as pd
import xarray as xr
import geopandas as gpd
import regionmask
import matplotlib.pyplot as plt
from xclim.core.indicator import registry
from dask.diagnostics import ProgressBar
import dask
import warnings
warnings.filterwarnings("ignore", message="Class CDD already exists and will be overwritten.")

def run(cfg):
    start_time = time.time()
    print("Starting CDD processing...")

    # Config
    lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
    lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]
    threshold  = cfg["cdd"].get("threshold_mm", 1.0)
    aggr       = cfg["cdd"].get("aggregation", "annual")

    aggr_map = {"monthly": "MS", "seasonal": "QS-DEC", "annual": "YS"}
    aggr_code = aggr_map.get(aggr, "YS")

    ROOT       = Path(__file__).resolve().parents[2]
    DATA_DIR   = ROOT / "data" / "pr"
    SHAPEFILE  = ROOT / "climate_regions" / "cleaned_clim_reg_2025_06_30.shp"
    TOWNS_CSV  = ROOT / "cities" / "cities.csv"

    print(" Loading shapefile and region mask...")
    bioregions = gpd.read_file(SHAPEFILE).to_crs("EPSG:4326")
    region_mask = regionmask.Regions(
        outlines=bioregions.geometry,
        names=bioregions["Veg_Biome"],
        abbrevs=bioregions["Veg_Biome"],
        name="Bioregions",
    )

    nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / "**/historical/*.nc"), recursive=True))
    print(f" Found {len(nc_files)} historical NetCDF files.\n")

    model_data, model_names = [], []

    CDD = registry.get("CDD")
    if CDD is None:
        raise RuntimeError("❌ 'CDD' indicator not registered in xclim.")

    for i, nc_file in enumerate(nc_files, 1):
        print(f" [{i}/{len(nc_files)}] Processing: {nc_file.name}")
        try:
            ds = xr.open_dataset(nc_file, chunks={"time": -1})
            ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))

            if "pr" not in ds:
                raise ValueError("Missing 'pr' variable in dataset.")

            pr = ds["pr"] * 86400.0
            pr.attrs["units"] = "mm/day"

            try:
                
                cdd_instance = CDD()  # instantiate the indicator
                pr.attrs["cell_methods"] = "time: mean"
                pr.attrs["standard_name"] = "precipitation_flux"
                cdd_result = cdd_instance(pr=pr, thresh=f"{threshold} mm/day", freq=aggr_code)
                cdd_result = cdd_result.compute()

                if cdd_result.isnull().all():
                    raise ValueError("All CDD values are NaN.")

                cdd_mean = cdd_result.mean(dim="time")
            except Exception as cdd_err:
                raise RuntimeError(f"❌ Failed during CDD calculation: {cdd_err}")

            mask_da = region_mask.mask(cdd_mean)
            reg_cdd = cdd_mean.groupby(mask_da).mean()

            try:
                idx = nc_file.parts.index("historical")
                model_name = nc_file.parts[idx - 1]
            except ValueError:
                model_name = nc_file.stem.split("_")[2]

            model_data.append(reg_cdd)
            model_names.append(model_name)


        except Exception as e:
            print(f"❌ Error processing {nc_file.name}: {e}\n")

    if not model_data:
        raise RuntimeError("❌ No datasets processed successfully.")

    print(" Computing ensemble means with Dask...")
    with ProgressBar():
        computed_data = dask.compute(*model_data)

    print(" Stacking results and calculating ensemble mean...")
    stack = xr.concat(computed_data, dim="model")
    stack["model"] = model_names
    ensemble_mean = stack.mean(dim="model")

    df = pd.DataFrame({
        "Bioregion": region_mask.names,
        "Max_CDD_days": ensemble_mean.values,
    }).sort_values("Max_CDD_days", ascending=False)

    print("\n Max Consecutive Dry Days (CDD) – Ensemble Mean")
    print(df.to_string(index=False))

    print("\n Generating plot...")
    bioregion_mean_df = pd.DataFrame({
        "Veg_Biome": region_mask.names,
        "Max_CDD_days": ensemble_mean.values,
    })
    merged = bioregions.merge(bioregion_mean_df, on="Veg_Biome")

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

    print(f"\nFinished in {time.time() - start_time:.1f} seconds.")