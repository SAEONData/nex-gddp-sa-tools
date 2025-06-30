#!/usr/bin/env python3
"""
CDD_Biomes.py
---------------------------------------------------------------
Calculate and plot CMIP6 maximum consecutive dry days (CDD)
aggregated by South-African vegetation biomes.

Project layout assumed
.
├── climate_regions/
│   └── cleaned_veg_biome_clim_reg.shp
├── cities/
│   └── cities.csv
├── data/
│   └── pr/<MODEL>/historical/*.nc
├── src/
│   ├── download_sa_subset.py
│   └── climate_indices/CDD_Biomes.py   ← this file
└── download_config.yml
"""
# ------------------------------------------------------------------ #
# 0. Imports                                                         #
# ------------------------------------------------------------------ #
from pathlib import Path
import sys, glob
import numpy as np
import pandas as pd
import xarray as xr
import geopandas as gpd
import regionmask
import matplotlib.pyplot as plt
import yaml

# ------------------------------------------------------------------ #
# 1. Locate repo root (has download_config.yml)                       #
# ------------------------------------------------------------------ #
def find_project_root(start: Path, marker: str = "download_config.yml") -> Path:
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Could not locate '{marker}' upwards from {start}")

SCRIPT = Path(__file__).resolve()
ROOT   = find_project_root(SCRIPT)

# ------------------------------------------------------------------ #
# 2. Make src/ importable                                            #
# ------------------------------------------------------------------ #
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from download_sa_subset import download_sa_bbox  # noqa: E402

# ------------------------------------------------------------------ #
# 3. Read YAML config                                                #
# ------------------------------------------------------------------ #
CFG_PATH = ROOT / "download_config.yml"
with CFG_PATH.open() as fh:
    cfg = yaml.safe_load(fh)

lat_bounds = [cfg["region"]["lat_min"], cfg["region"]["lat_max"]]
lon_bounds = [cfg["region"]["lon_min"], cfg["region"]["lon_max"]]

# ------------------------------------------------------------------ #
# 4. Static paths                                                    #
# ------------------------------------------------------------------ #
DATA_DIR        = ROOT / "data" / "pr"
SHAPEFILE       = ROOT / "climate_regions" / "cleaned_veg_biome_clim_reg.shp"
TOWNS_CSV       = ROOT / "cities" / "cities.csv"

# ------------------------------------------------------------------ #
# 5. Region mask                                                     #
# ------------------------------------------------------------------ #
bioregions = gpd.read_file(SHAPEFILE).to_crs("EPSG:4326")
region_mask = regionmask.Regions(
    outlines=bioregions.geometry,
    names=bioregions["Veg_Biome"],
    abbrevs=bioregions["Veg_Biome"],
    name="Bioregions",
)

# ------------------------------------------------------------------ #
# 6. Helper – max consecutive dry days                               #
# ------------------------------------------------------------------ #
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

# ------------------------------------------------------------------ #
# 7. Collect NetCDFs                                                 #
# ------------------------------------------------------------------ #
nc_files = sorted(Path(p).resolve() for p in glob.glob(str(DATA_DIR / "**/historical/*.nc"), recursive=True))
print(f"Found {len(nc_files)} historical NetCDF files.")

# ------------------------------------------------------------------ #
# 8. Process each file                                               #
# ------------------------------------------------------------------ #
model_data = []
model_names = []

for nc_file in nc_files:
    try:
        ds = xr.open_dataset(nc_file)

        # subset bbox
        ds = ds.sel(lat=slice(*lat_bounds), lon=slice(*lon_bounds))

        # pr (kg m-2 s-1) → mm/day
        pr = ds["pr"] * 86400.0

        # annual max CDD
        cdd_annual = pr.resample(time="YE").map(max_cdd)
        cdd_mean   = cdd_annual.mean(dim="time")

        # mask & region aggregate
        mask_da   = region_mask.mask(cdd_mean)
        reg_cdd   = cdd_mean.groupby(mask_da).mean()

        model_data.append(reg_cdd)

        # derive model name
        try:
            idx = nc_file.parts.index("historical")
            model_names.append(nc_file.parts[idx - 1])
        except ValueError:
            model_names.append(nc_file.stem.split("_")[2])  # fallback

        print(f"✅ Processed: {nc_file.name}")

    except Exception as exc:
        print(f"❌ Error processing {nc_file.name}: {exc}")

# ------------------------------------------------------------------ #
# 9. Ensemble mean                                                  #
# ------------------------------------------------------------------ #
if not model_data:
    raise RuntimeError("No datasets processed successfully.")

stack = xr.concat(model_data, dim="model")
stack["model"] = model_names
ensemble_mean = stack.mean(dim="model")

df = pd.DataFrame({
    "Bioregion": region_mask.names,
    "Max_CDD_days": ensemble_mean.values,
}).sort_values("Max_CDD_days", ascending=False)

print("\n📊 Max Consecutive Dry Days (CDD) – Ensemble Mean")
print(df.to_string(index=False))

# ------------------------------------------------------------------ #
# 10. Plot                                                          #
# ------------------------------------------------------------------ #
bioregion_mean_df = pd.DataFrame({
    "Veg_Biome": region_mask.names,
    "Max_CDD_days": ensemble_mean.values,
})
merged = bioregions.merge(bioregion_mean_df, on="Veg_Biome")

towns_df  = pd.read_csv(TOWNS_CSV, sep=";").rename(columns=str.strip)
towns_gdf = gpd.GeoDataFrame(
    towns_df,
    geometry=gpd.points_from_xy(towns_df["lng"], towns_df["lat"]),
    crs="EPSG:4326",
)

fig, ax = plt.subplots(figsize=(10, 8))
vmin, vmax = 0, merged["Max_CDD_days"].max()
ticks = range(int(vmin), int(vmax) + 10, 10)

merged.plot(
    column="Max_CDD_days",
    cmap="YlOrBr",
    linewidth=0.8,
    edgecolor="black",
    legend=True,
    legend_kwds={
        "label": "Max Consecutive Dry Days (CDD)",
        "orientation": "vertical",
        "ticks": ticks,
    },
    ax=ax,
)

towns_gdf.plot(ax=ax, color="red", markersize=40, zorder=5)
for x, y, label in zip(towns_gdf.geometry.x, towns_gdf.geometry.y, towns_gdf["city"]):
    ax.text(x, y, label, fontsize=9, ha="left", va="bottom")

ax.set_title("CMIP6 Max Consecutive Dry Days by Vegetation Biome (1950-2014)", fontsize=14)
ax.set_axis_off()
plt.tight_layout()
plt.show()