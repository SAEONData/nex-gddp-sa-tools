# nex-gddp-sa-tools
Helper scripts for exploring, downloading, and analysing NASA NEX-GDDP-CMIP6 data for South Africa (SAWS + SAEON).

## Catalogue explorer

The script **`src/nex_gddp_catalog.py`** lets you see exactly what’s in the
NASA NEX-GDDP-CMIP6 archive without opening a web browser.

```bash
# List every CMIP6 model
python src/nex_gddp_catalog.py models

# Drill into one model: experiments → runs → variable codes
python src/nex_gddp_catalog.py info ACCESS-CM2

# Just show the variable codes for a specific run
python src/nex_gddp_catalog.py vars ACCESS-CM2 ssp585 r1i1p1f1
