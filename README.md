# nex-gddp-sa-tools
Helper scripts for exploring, downloading, and analysing **NASA NEX‑GDDP‑CMIP6** down‑scaled climate projections for the South‑African domain (SAWS + SAEON).

* **Catalogue explorer** – list models, experiments, runs, variables  
* **Bulk downloader** – fetch daily precipitation & temperature for a South‑Africa bounding box via YAML parameters  
* **Climate indices (modular)** – calculate CDD and other indices by vegetation biome via YAML configs and model ensemble  
* **(Coming soon)** web visualisation & map export tools
* 
> **Data source**  
> NASA Earth Exchange Global Daily Downscaled Projections (NEX‑GDDP‑CMIP6) served via NCCS THREDDS.

---

## Quick‑start

```bash
git clone https://github.com/<org>/nex-gddp-sa-tools.git
cd nex-gddp-sa-tools
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 1  Catalogue explorer  
_Last verified: **19 May 2025**_

```bash
# List all CMIP6 models
python src/nex_gddp_catalog.py models
```

<details>
<summary>Sample output (first 10 of 35 models)</summary>

```
ACCESS-CM2
ACCESS-ESM1-5
BCC-CSM2-MR
CanESM5
CESM2
CESM2-WACCM
CMCC-CM2-SR5
CMCC-ESM2
CNRM-CM6-1
CNRM-ESM2-1
… (25 more)
```
</details>

```bash
# Experiments, ensemble runs & variables for one model
python src/nex_gddp_catalog.py info ACCESS-CM2
```

```
historical:
  r1i1p1f1: hurs, huss, pr, rlds, rsds, sfcWind, tas, tasmax, tasmin
ssp126:
  r1i1p1f1: hurs, huss, pr, rlds, rsds, sfcWind, tas, tasmax, tasmin
...
```

```bash
# List variable codes for a specific run
python src/nex_gddp_catalog.py vars ACCESS-CM2 ssp585 r1i1p1f1
```



```bash
# List all file names (all versions)
# Syntax:
# python src/nex_gddp_catalog.py files <MODEL> <EXPERIMENT> <RUN> <VARIABLE>

python src/nex_gddp_catalog.py files ACCESS-CM2 historical r1i1p1f1 pr
```
---

## 2  Bulk download with YAML config

1. **Edit the config:**

```bash
e.g. vim download_config.yml   # edit region, years, models, variables
```

2. **Run the driver:**

```bash
python src/run_downloads.py            # or  python -m src.run_downloads
```

The script reads `download_config.yml`, loops over every  
`model × experiment × variable`, and saves files under:

```
data/<variable>/<model>/<experiment>/<variable>_<year>.nc
```

### One‑off test download

```bash
python src/download_sa_subset.py     --model ACCESS-CM2     --experiment historical     --variable pr     --start 2010 --end 2014
```

---

## 3  Climate indices (e.g. CDD)

The `src/climate_indices/` folder contains modular scripts that compute climate indices using NEX‑GDDP data and aggregate them by **vegetation biome**, using region masks and shapefiles.

### ✅ Currently implemented:
- `CDD (Consecutive Dry Days)` – configurable threshold and time aggregation
- `More indices coming soon...` (e.g. PRCPTOT, RX5day, TXx)

---

### a. Configure `climate_indices_config.yml`

```yaml
region:
  lat_min: -35.0
  lat_max: -20.0
  lon_min: 16.0
  lon_max: 33.0

run_indices:
  - cdd          # Add more indices like 'prcptot', 'rx5day', etc.

cdd:
  threshold_mm: 1.0       # Precip threshold (mm/day)
  aggregation: annual     # Options: annual, monthly, seasonal
  output_units: days
```

---

### b. Run all configured indices

```bash
python src/run_climate_indices.py
```

Each index must be defined in a module named `<index>_biomes.py` with a `run(cfg)` function.

Example:
- CDD → `src/climate_indices/cdd_biomes.py`

Each module:
- Loads the shapefile (`cleaned_clim_reg_*.shp`)
- Applies the region mask
- Aggregates per model
- Calculates ensemble means
- Plots maps and prints regional results

---

## Requirements

```
requests>=2.31
xarray>=2024.5
netCDF4>=1.6
pyyaml>=6.0        # NEW – parses YAML configs
```

Install / update:

```bash
pip install -r requirements.txt
```

---

## Licence

TODO
