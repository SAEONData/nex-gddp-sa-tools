# nex-gddp-sa-tools
Helper scripts for exploring, downloading, and analysing **NASA NEX‑GDDP‑CMIP6** down‑scaled climate projections for the South‑African domain (SAWS + SAEON).

* **Catalogue explorer** – list models, experiments, runs, variables  
* **Bulk downloader** – fetch daily precipitation & temperature for a South‑Africa bounding box via YAML parameters  
* **(Coming soon)** climate‑index calculator & ensemble aggregation

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

---

## 2  Bulk download with YAML config

1. **Create your config:**

```bash
cp download_config_template.yml download_config.yml
vim download_config.yml   # edit region, years, models, variables
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
