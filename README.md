# nex-gddp-sa-tools
Helper scripts for exploring, downloading, and analysing **NASA NEX-GDDP-CMIP6** down-scaled climate projections for the South-African domain (SAWS + SAEON).

---

## Climate Indices – Reference & Plotting Guide

This project computes a range of rainfall and temperature indices from NASA NEX-GDDP-CMIP6 downscaled climate data for South Africa.  

For each index, you can plot **absolute values** (climatology) or **anomalies** (change relative to a baseline period).

---

### How to Read Anomalies

- **Positive anomaly** = increase relative to baseline  
- **Negative anomaly** = decrease relative to baseline  
- Some indices are “beneficial” when lower (e.g., fewer frost days) — so a **negative anomaly** can still indicate warming.  
- Always check the “Anomaly Interpretation” notes to understand what each sign means in context.

---

## Rainfall Indices

| Short Name  | Description                                              | Units    | Anomaly Interpretation | Suggested Colours |
| ----------- | -------------------------------------------------------- | -------- | ---------------------- | ----------------- |
| **CDD**     | Max consecutive dry days (PR < 1 mm/day)                 | days     | Positive = longer dry spells, Negative = shorter dry spells | Red = longer (drier), Blue = shorter (wetter) |
| **CWD**     | Max consecutive wet days (PR ≥ 1 mm/day)                  | days     | Positive = longer wet spells, Negative = shorter wet spells | Blue = longer (wetter), Red = shorter (drier) |
| **R10mm**   | Days with ≥ 10 mm rainfall                               | days     | Positive = more heavy-rain days, Negative = fewer heavy-rain days | Blue = more, Red = fewer |
| **R20mm**   | Days with ≥ 20 mm rainfall                               | days     | Positive = more very-heavy-rain days, Negative = fewer very-heavy-rain days | Blue = more, Red = fewer |
| **PRCPTOT** | Annual rainfall on wet days (PR ≥ 1 mm/day)              | mm       | Positive = wetter years, Negative = drier years | Blue = wetter, Red = drier |
| **R95pTOT** | % of rainfall from very wet days (>95th percentile)      | %        | Positive = larger share from very wet days, Negative = smaller share | Blue = larger share, Red = smaller share |
| **R99pTOT** | % of rainfall from extremely wet days (>99th percentile) | %        | Positive = larger share from extremely wet days, Negative = smaller share | Blue = larger share, Red = smaller share |
| **R95p**    | Total rainfall from very wet days (>95th percentile)     | mm       | Positive = more rain from very wet days, Negative = less rain from very wet days | Blue = more, Red = less |
| **R99p**    | Total rainfall from extreme days (>99th percentile)      | mm       | Positive = more rain from extreme days, Negative = less rain from extreme days | Blue = more, Red = less |
| **SPI**     | Standardised Precipitation Index (3, 6, 12 months)       | unitless | Positive = wetter than average, Negative = drier than average | Blue = wetter, Red = drier |
| **SDII**    | Rainfall intensity on wet days                           | mm/day   | Positive = higher rainfall intensity, Negative = lower intensity | Blue = higher, Red = lower |
| **Rx1day**  | Wettest day of the year                                  | mm       | Positive = more intense wettest day, Negative = less intense wettest day | Blue = more intense, Red = less intense |
| **Rx5day**  | Wettest 5-day period of the year                         | mm       | Positive = more intense wettest 5-day period, Negative = less intense | Blue = more intense, Red = less intense |

---

## Temperature Indices

| Short Name  | Description                                                       | Units    | Anomaly Interpretation | Suggested Colours |
| ----------- | ----------------------------------------------------------------- | -------- | ---------------------- | ----------------- |
| **FD**      | Days with min temp < 0°C (frost days)                             | days     | Positive = more frost days (cooling), Negative = fewer frost days (warming) | Blue = more frost, Red = fewer frost (warming) |
| **TNlt2**   | Days with min temp < 2°C                                          | days     | Positive = more near-frost days (cooling), Negative = fewer near-frost days (warming) | Blue = more, Red = fewer (warming) |
| **TXx**     | Warmest daily max temperature                                     | °C       | Positive = hotter extremes, Negative = cooler extremes | Red = hotter, Blue = cooler |
| **TNn**     | Coldest daily min temperature                                     | °C       | Positive = warmer coldest nights, Negative = colder coldest nights | Red = warmer, Blue = colder |
| **WSDI**    | Warm spell duration: TX > 90th percentile for ≥6 days             | days     | Positive = more warm-spell days, Negative = fewer warm-spell days | Red = more, Blue = fewer |
| **CSDI**    | Cold spell duration: TN < 10th percentile for ≥6 days             | days     | Positive = more cold-spell days (cooling), Negative = fewer cold-spell days (warming) | Blue = more, Red = fewer (warming) |
| **TXgt50p** | % of days with TX > 50th percentile                               | %        | Positive = more warm days, Negative = fewer warm days | Red = more, Blue = fewer |
| **TXge30**  | Days with TX ≥ 30°C                                               | days     | Positive = more hot days, Negative = fewer hot days | Red = more, Blue = fewer |
| **TXdTNd**  | Consecutive days with both TX & TN > 95th percentile              | events   | Positive = more heatwave events, Negative = fewer events | Red = more, Blue = fewer |
| **TNx**     | Warmest daily minimum temperature (hottest night)                 | °C       | Positive = warmer hottest nights, Negative = cooler hottest nights | Red = warmer, Blue = cooler |
| **TXn**     | Coldest daily maximum temperature (coldest day)                   | °C       | Positive = warmer coldest days, Negative = colder coldest days | Red = warmer, Blue = colder |
| **TX10p**   | % of days with TX < 10th percentile (cool days)                   | %        | Positive = more cool days (cooling), Negative = fewer cool days (warming) | Blue = more, Red = fewer (warming) |
| **TX90p**   | % of days with TX > 90th percentile (hot days)                    | %        | Positive = more hot days, Negative = fewer hot days | Red = more, Blue = fewer |
| **TN10p**   | % of days with TN < 10th percentile (cold nights)                 | %        | Positive = more cold nights (cooling), Negative = fewer cold nights (warming) | Blue = more, Red = fewer (warming) |
| **TN90p**   | % of days with TN > 90th percentile (warm nights)                 | %        | Positive = more warm nights, Negative = fewer warm nights | Red = more, Blue = fewer |


* **Catalogue explorer** – list models, experiments, runs, variables  
* **Bulk downloader** – fetch daily precipitation & temperature for a South‑Africa bounding box via YAML parameters  

* **Download verification** – check how many NetCDFs have been downloaded and visualise % completeness
* **Climate indices (modular)** – calculate indices via YAML configs and model ensemble
* **plotting** – generate by-bioregion time slice plots using `plot_config.yml`
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

## 2  Bulk download with YAML config
_Last update: **03 July 2025**_
> **Note:** `download_config.yml` has been extended to include **all models relevant to this project**, plus a `grid_label_default` and per-model `grid_labels` map to handle GN/GR/GR1/GR2 naming, and per-experiment time ranges (historical 1949–2014, SSPs 2015–2100).

1. **Edit the config:**

   ```bash
   vim download_config.yml   #Lists models, default + exceptions for grid_label, and time_ranges

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





### a. Configure `climate_indices_config.yml`

```yaml
# download_config.yml
# --------------------------------------------------------------------
# Parameters controlling what to DOWNLOAD from NEX-GDDP-CMIP6.
# Copy this into your repo (overwrite download_config_template.yml),
# then edit as required.
# --------------------------------------------------------------------
meta:
  owner: "SAEON Climate Team"
  created: "2025-05-19"
  purpose: "Define spatial / temporal subset and model list for download"

region:
  name: "South Africa mainland"
  lat_min: -35.0
  lat_max: -21.0
  lon_min: 16.0
  lon_max: 33.0
  stride: 1        # 1 = native 0.25°, 2 = every second grid cell

time:
  # used only for 'historical'
  start_year: 1950
  end_year: 2014

models:
  select:
    - ACCESS-CM2
    - ACCESS-ESM1-5
    - BCC-CSM2-MR
    #- CESM2
    #- CESM2-WACCM
    - CMCC-CM2-SR5
    - CMCC-ESM2
    #- CNRM-CM6-1
    #- CNRM-ESM2-1
    - CanESM5
    - EC-Earth3
    - EC-Earth3-Veg-LR
    - FGOALS-g3
    - GFDL-CM4
    - GFDL-CM4_gr2
    - GFDL-ESM4
    #- GISS-E2-1-G
    #- HadGEM3-GC31-LL
    #- HadGEM3-GC31-MM
    - IITM-ESM
    - INM-CM4-8
    - INM-CM5-0
    - IPSL-CM6A-LR
    - KACE-1-0-G
    - KIOST-ESM
    - MIROC6
    - MPI-ESM1-2-HR
    - MPI-ESM1-2-LR
    - MRI-ESM2-0
    - NESM3
    - NorESM2-LM
    - NorESM2-MM
    - TaiESM1
    #- UKESM1-0-LL

# default grid label if a model is not overridden below
grid_label_default: gn

# per-model grid-label overrides
grid_labels:
  EC-Earth3: gr
  EC-Earth3-Veg-LR: gr
  GFDL-CM4: gr1
  GFDL-CM4_gr2: gr2
  GFDL-ESM4: gr1
  INM-CM4-8: gr1
  INM-CM5-0: gr1
  IPSL-CM6A-LR: gr
  KACE-1-0-G: gr
  KIOST-ESM: gr1

experiments:
  select:
    - historical
    - ssp126
    - ssp245
    - ssp370
    - ssp585

  # define per-experiment time spans
  time_ranges:
    historical: [1950, 2014]
    ssp126:     [2015, 2100]
    ssp245:     [2015, 2100]
    ssp370:     [2015, 2100]
    ssp585:     [2015, 2100]

variables:
  daily:
    - pr        # precipitation
    - tasmax    # daily max temp
    - tasmin    # daily min temp

run: "r1i1p1f1"    # ensemble member
```

---


## 3  Download verification summary  
_Last update: **03 July 2025**_

Automatically checks how many NetCDF files were successfully downloaded for each:
- Model × Variable × Scenario  
- Compared against expected time ranges
- ✅ if all are complete
- 🔴 if there are more files than expected - indicates possible diffrent versions
- ⚠️ incomplete downloads   
- Visualised as completeness 

### ▶️ Run verification

```bash
python src/verify_downloads.py
```

Generates:
- CSV summary tables

Example (partial):

```
Model         Variable   historical (65)   ssp126 (85)   ssp245 (85)   ssp370 (85)   ssp585 (85)   Total     Overall
--------------------------------------------------------------------------------------------------------------
ACCESS-CM2    pr         ✅                ⚠️ 83/85       ⚠️ 12/85       ⚠️ 0/85        ⚠️ 0/85        163/405   ⚠️
ACCESS-CM2    tasmax     ✅                ⚠️ 82/85       ⚠️  0/85       ⚠️ 0/85        ⚠️ 0/85        151/405   ⚠️
ACCESS-CM2    tasmin     ✅                🔴 87/86       ⚠️  0/86       ⚠️ 0/86        ⚠️ 0/86        152/409   ⚠️
...
```

---

## 4  Climate indices (e.g. CDD)

The `src/climate_indices/` folder contains modular scripts that compute climate indices using NEX‑GDDP data and aggregate them by **vegetation biome**, using region masks and shapefiles.

### ✅ Currently implemented:
- `CDD (Consecutive Dry Days)` – configurable threshold and time aggregation
- `R10mm`, `R20mm`, `PRCPTOT`, `R95pTOT`, etc.  (see table below)
- `More temperature-based indices coming soon...`

---


### 📋 List of relevant Climpact indices used in precipitation trend analysis
| Short name | Long name                         | Definition                                 | Plain language description                                 | Units   | 
|------------|------------------------------------|--------------------------------------------|-------------------------------------------------------------|---------|
| CDD        | Consecutive Dry Days               | Max # of consecutive days with PR < 1 mm   | Longest dry spell                                            | days    | 
| R10mm      | Heavy rain days                    | Days when PR ≥ 10 mm                       | Days with at least 10 mm rain                               | days    | 
| R20mm      | Very heavy rain days               | Days when PR ≥ 20 mm                       | Days with at least 20 mm rain                               | days    |
| PRCPTOT    | Total wet-day PR                   | Sum of daily PR ≥ 1 mm                     | Total rainfall from wet days                                | mm      | 
| R95pTOT    | Very wet day contribution          | 100 × R95p / PRCPTOT                       | % of rainfall from days above 95th percentile               | %       |
| R99pTOT    | Extremely wet day contribution     | 100 × R99p / PRCPTOT                       | % of rainfall from days above 99th percentile               | %       |
| SPI        | Standardised Precipitation Index   | Standardised precipitation deficit measure | Drought severity on 3/6/12-month time scales                | –       | 
| CWD        | Consecutive Wet Days               | Max # of consecutive days with PR ≥ 1 mm   | Longest wet spell                                            | days    |
| SDII       | Simple daily intensity index       | Total PR / # wet days (PR ≥ 1 mm)          | Avg. rainfall intensity on wet days                         | mm/day  |
| R95p       | Total rainfall from very wet days  | Sum of daily PR > 95th percentile          | Total rainfall from very wet days                           | mm      |
| R99p       | Total rainfall from extreme days   | Sum of daily PR > 99th percentile          | Total rainfall from extremely wet days                      | mm      | 
| Rx1day     | Max 1-day precipitation            | Max daily PR total                         | Most rainfall on a single day                               | mm      | 
| Rx5day     | Max 5-day precipitation            | Max 5-day PR total                         | Most rainfall over 5 consecutive days                       | mm      | 

---

## 5  Plotting  
_Last update: **07 Aug 2025**_

The `src/plot_index_by_bioregion.py` module generates time-slice maps (by vegetation biome) from ensemble NetCDFs.

### ▶️ Run plotting

```bash
python src/run_plot_example.py
```

Produces:
- `max_cdd_historical_baseline.png`
- `max_cdd_scenarios_timeslices.png`

Both stored in the directory specified under `output_dir` in the YAML.

### 🧾 `plot_config.yml`

```yaml
paths:
  shapefile: "climate_regions/cleaned_clim_reg_2025_06_30.shp"
  towns_csv: "cities/cities.csv"
  output_dir: "data/outputs/plots"

scenarios:
  - historical
  - ssp126
  - ssp245
  - ssp370
  - ssp585

indices:
  - name: cdd
    variable: max_cdd
    data_dir: "data/outputs/cdd"
    output: "cdd_bioregion_timeslices.png"
    cmap: YlOrBr
    legend_label: "Max Consecutive Dry Days (days)"
```

### Functions

- `run_plot_example.py`: loops over indices and calls plotting
- `plot_index_by_bioregion.py`: loads shapefiles, applies region masks, averages values, creates plots

---

## Requirements

Install / update:

```bash
pip install -r requirements.txt
```

---

## Licence

TODO
