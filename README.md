# nex-gddp-sa-tools
Helper scripts for exploring, downloading, and analysing **NASA NEX-GDDP-CMIP6** down‑scaled climate projections, pre‑configured for the South‑African domain (SAWS + SAEON).

* **Catalogue explorer** – list models, experiments, runs, variables  
* **SA downloader** – grab daily precipitation / temperature for South Africa (lat −35 → −21 °, lon 16 → 33 °)

> **Data source**  
> NASA Earth Exchange Global Daily Downscaled Projections (NEX‑GDDP‑CMIP6) via NCCS THREDDS.

---

## Quick‑start

```bash
git clone https://github.com/<org>/nex-gddp-sa-tools.git
cd nex-gddp-sa-tools
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## Catalogue explorer  
_Last verified: **19 May 2025**_

The script **`src/nex_gddp_catalog.py`** lets you inspect the catalogue.

### 1  List all CMIP6 models

```bash
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

### 2  Drill into one model

```bash
python src/nex_gddp_catalog.py info ACCESS-CM2
```

```
historical:
  r1i1p1f1: pr, tasmax, tasmin
ssp245:
  r1i1p1f1: pr, tasmax, tasmin
ssp585:
  r1i1p1f1: pr, tasmax, tasmin
```

### 3  Show variable codes for a run

```bash
python src/nex_gddp_catalog.py vars ACCESS-CM2 ssp585 r1i1p1f1
```

```
pr, tasmax, tasmin
```

---

## South‑Africa subset downloader

```bash
python src/download_sa_subset.py \
    --model ACCESS-CM2 \
    --experiment historical \
    --variable pr \
    --start 2010 --end 2014
```

Outputs NetCDFs to `data/<variable>/<model>/<experiment>/`.

---

## Requirements

```
requests
xarray
netCDF4
```

Install via:

```bash
pip install -r requirements.txt
```

---

## Licence

MIT – see `LICENSE`.
