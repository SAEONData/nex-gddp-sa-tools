#!/usr/bin/env python3
"""
run_plot_time_slices.py
---------------------------------------------------
Reads plotting config and generates regional time-slice plots
for one or more climate indices using overlay maps only.
Supports both ensemble means and anomalies.

Notes:
- We now open datasets with decode_timedelta=False.
- vmin/vmax: if present in YAML, they are passed through.
  Otherwise we pass None so overlay functions auto-scale
  from the *masked* data inside the shapefile boundary.
"""

import sys
import yaml
from pathlib import Path
import xarray as xr
import numpy as np

def find_root(start: Path, marker="plot_config.yml") -> Path:
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Cannot find '{marker}' upward from {start}")

SCRIPT = Path(__file__).resolve()
ROOT = find_root(SCRIPT)
CFG_PATH = ROOT / "plot_config.yml"
SRC_DIR = ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Load config
with CFG_PATH.open() as f:
    cfg = yaml.safe_load(f)

# Import plotters
from plot_index_by_bioregion_overlay import plot_time_slices_by_bioregion_overlay
from plot_index_by_municipality_overlay import plot_time_slices_by_municipality_overlay

# Paths
shape_path = (ROOT / cfg["paths"]["shapefile"]).resolve()
shape2_path = (ROOT / cfg["paths"]["shapefile2"]).resolve()
towns_path = (ROOT / cfg["paths"]["towns_csv"]).resolve()  # kept for signature compatibility
output_dir = (ROOT / cfg["paths"].get("output_dir", "data/outputs/plots")).resolve()
output_dir.mkdir(parents=True, exist_ok=True)

scenarios = cfg.get("scenarios", [])
future_scenarios = scenarios[1:]  # skip historical
future_labels = [
    "Near-term (2021–2040)",
    "Mid-term (2041–2060)",
    "Far-term (2081–2100)"
]

indices = cfg.get("indices", [])
if not indices:
    print("⚠️ No indices specified in config under `indices:`. Nothing to plot.")
    sys.exit(0)

for entry in indices:
    index_name = entry["name"]
    index_var = entry.get("variable", f"max_{index_name}")
    is_anomaly = entry.get("anomaly", True)
    input_dir = (ROOT / entry.get("data_dir", f"data/outputs/{index_name}")).resolve()
    cmap = entry.get("cmap", "RdBu_r")
    legend_label = entry.get(
        "legend_label",
        f"{index_var.replace('_', ' ').title()} {'Anomaly (units)' if is_anomaly else '(units)'}",
    )

    # Respect YAML vmin/vmax if provided; otherwise let overlay auto-scale from masked data
    vmin_cfg = entry.get("vmin", None)
    vmax_cfg = entry.get("vmax", None)

    mode = "ANOMALY" if is_anomaly else "ENSEMBLE MEAN"
    print(f"\n📊 Plotting {mode}: {index_name.upper()} → variable={index_var}, input={input_dir}")

    # Call overlay plot functions (they handle reading files and masking)
    plot_time_slices_by_bioregion_overlay(
        index_name=index_name,
        data_dir=input_dir,
        shapefile_path=shape_path,
        towns_csv_path=towns_path,       # unused inside, kept for API compatibility
        index_variable=index_var,
        time_labels=future_labels,
        scenario_order=future_scenarios,
        cmap=cmap,
        output_path=output_dir,
        legend_label=legend_label,
        vmin=vmin_cfg,
        vmax=vmax_cfg,
        anomaly=is_anomaly,
    )

    plot_time_slices_by_municipality_overlay(
        index_name=index_name,
        data_dir=input_dir,
        shapefile_path=shape2_path,
        towns_csv_path=towns_path,       # unused inside, kept for API compatibility
        index_variable=index_var,
        time_labels=future_labels,
        scenario_order=future_scenarios,
        cmap=cmap,
        output_path=output_dir,
        legend_label=legend_label,
        vmin=vmin_cfg,
        vmax=vmax_cfg,
        anomaly=is_anomaly,
    )