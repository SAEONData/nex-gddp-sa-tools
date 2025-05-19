#!/Users/privateprivate/SARVA_ws/bin/python

#!/usr/bin/env python3
"""run_downloads.py
Bulk‑download NEX‑GDDP‑CMIP6 data for South Africa
using parameters from download_config.yml.

Usage:
        python src/run_downloads.py      # assumes config file in repository root

The script:
    1. loads YAML config
    2. loops over models × experiments × variables
    3. calls the reusable download_sa_bbox() function

Requirements:
        pip install requests xarray netCDF4 pyyaml
"""

from pathlib import Path
import yaml
from download_sa_subset import download_sa_bbox

# ------------------------------------------------------------------ #
# 1. Locate configuration file                                       #
# ------------------------------------------------------------------ #
CFG_PATH = Path(__file__).resolve().parents[1] / "download_config.yml"
if not CFG_PATH.exists():
        raise FileNotFoundError(
                f"Config file {CFG_PATH} not found. "
                "Copy download_config_template.yml to download_config.yml and edit first."
        )
    
with CFG_PATH.open() as fh:
        cfg = yaml.safe_load(fh)
    
# ------------------------------------------------------------------ #
# 2. Build bounding‑box dict from config                             #
# ------------------------------------------------------------------ #
bbox = dict(
        north=cfg["region"]["lat_max"],
        south=cfg["region"]["lat_min"],
        west=cfg["region"]["lon_min"],
        east=cfg["region"]["lon_max"],
)

stride = cfg["region"].get("stride", 1)
run_id = cfg.get("run", "r1i1p1f1")

# ------------------------------------------------------------------ #
# 3. Iterate and download                                            #
# ------------------------------------------------------------------ #
for model in cfg["models"]["select"]:
        for exp in cfg["experiments"]["select"]:
                for var in cfg["variables"]["daily"]:
                        print(f"\n=== {model} | {exp} | {var} ===")
                        download_sa_bbox(
                                model=model,
                                experiment=exp,
                                variable=var,
                                start=cfg["time"]["start_year"],
                                end=cfg["time"]["end_year"],
                                run=run_id,
                                bbox=bbox,
                                stride=stride,
                                out_root=Path("data"),
                        )
print("\nAll downloads completed.")
