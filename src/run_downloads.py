#!/usr/bin/env python3
# src/run_downloads.py

"""
Bulk-download NEX-GDDP-CMIP6 data for South Africa
using parameters from download_config.yml.
"""

import yaml
from pathlib import Path
from download_sa_subset import download_sa_bbox

# 1. Locate config file
CFG_PATH = Path(__file__).resolve().parents[1] / "download_config.yml"
if not CFG_PATH.exists():
    raise FileNotFoundError(
        f"Config file {CFG_PATH} not found. "
        "Copy download_config_template.yml to download_config.yml and edit first."
    )

with CFG_PATH.open() as fh:
    cfg = yaml.safe_load(fh)

# 2. Build bounding-box & defaults
bbox      = dict(
    north=cfg["region"]["lat_max"],
    south=cfg["region"]["lat_min"],
    west =cfg["region"]["lon_min"],
    east =cfg["region"]["lon_max"],
)
stride   = cfg["region"].get("stride", 1)
run_id   = cfg.get("run", "r1i1p1f1")

# 3. Grid-label logic
default_gl = cfg.get("grid_label_default", "gn")
grid_map   = cfg.get("grid_labels", {})

# 4. Experiment time-ranges
#    fallback to top-level time if missing
exp_time = cfg["experiments"].get("time_ranges", {})
hist_range = exp_time.get("historical",
                          [cfg["time"]["start_year"], cfg["time"]["end_year"]])

# 5. Iterate and download
for model in cfg["models"]["select"]:
    gl = grid_map.get(model, default_gl)

    for exp in cfg["experiments"]["select"]:
        start, end = exp_time.get(exp, hist_range)

        for var in cfg["variables"]["daily"]:
            print(f"\n=== {model} | {exp} | {var} | years {start}–{end} | grid ={gl} ===")
            download_sa_bbox(
                model=model,
                experiment=exp,
                variable=var,
                start=start,
                end=end,
                run=run_id,
                grid_label=gl,
                bbox=bbox,
                stride=stride,
                out_root=Path("data"),
            )

print("\nAll downloads completed.")