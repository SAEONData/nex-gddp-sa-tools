#!/usr/bin/env python3
"""
run_plot_time_slices.py
---------------------------------------------------
Reads plotting config and generates regional timeslice plots
for one or more climate indices.
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

# 2. Add src to path
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 3. Load YAML config
with CFG_PATH.open() as f:
    cfg = yaml.safe_load(f)

# 4. Import plotting functions
from plot_index_by_bioregion import plot_time_slices_by_bioregion
from plot_index_by_municipality import plot_time_slices_by_municipality
from plot_index_by_bioregion_overlay import plot_time_slices_by_bioregion_overlay

# 5. Shared paths
shape_path = (ROOT / cfg["paths"]["shapefile"]).resolve()
shape2_path = (ROOT / cfg["paths"]["shapefile2"]).resolve()
towns_path = (ROOT / cfg["paths"]["towns_csv"]).resolve()
output_dir = (ROOT / cfg["paths"].get("output_dir", "data/outputs/plots")).resolve()
output_dir.mkdir(parents=True, exist_ok=True)

# 6. Shared settings
scenarios = cfg.get("scenarios", [])
time_labels = [
    "Baseline (1995–2014)",
    "Near-term (2021–2040)",
    "Mid-term (2041–2060)",
    "Far-term (2081–2100)"
]
indices = cfg.get("indices", [])

if not indices:
    print("⚠️ No indices specified in config under `indices:`. Nothing to plot.")
    sys.exit(0)

# 7. Loop over indices and generate plots
for entry in indices:
    index_name = entry["name"]
    index_var = entry.get("variable", f"max_{index_name}")
    input_dir = (ROOT / entry.get("data_dir", f"data/outputs/{index_name}")).resolve()
    cmap = entry.get("cmap", "YlOrBr")
    legend_label = entry.get("legend_label", f"{index_var.replace('_', ' ').title()} (units)")
    spatial_agg = entry.get("spatial_agg", "mean")  # ✅ Default to 'mean' if not specified

    print(f"\n📊 Plotting {index_name.upper()} → variable={index_var}, input={input_dir}, agg={spatial_agg}")

    # 7.1 Compute global vmin/vmax from full gridded datasets
    all_files = list(input_dir.glob("*.nc"))
    all_values = []
    for f in all_files:
        ds = xr.open_dataset(f)
        if index_var in ds:
            da = ds[index_var]
        elif len(ds.data_vars) == 1:
            da = list(ds.data_vars.values())[0]
        else:
            continue
        all_values.extend(da.values.flatten())

    all_values = np.array(all_values)
    global_vmin = np.nanmin(all_values)
    global_vmax = np.nanmax(all_values)

    # 7.2 Call all plotting functions with consistent color scale
    plot_time_slices_by_bioregion(
        index_name=index_name,
        data_dir=input_dir,
        shapefile_path=shape_path,
        towns_csv_path=towns_path,
        index_variable=index_var,
        time_labels=time_labels,
        scenario_order=scenarios,
        cmap=cmap,
        output_path=output_dir,
        legend_label=legend_label,
        spatial_agg=spatial_agg,
        vmin=global_vmin,
        vmax=global_vmax,
    )

    plot_time_slices_by_municipality(
        index_name=index_name,
        data_dir=input_dir,
        shapefile_path=shape2_path,
        towns_csv_path=towns_path,
        index_variable=index_var,
        time_labels=time_labels,
        scenario_order=scenarios,
        cmap=cmap,
        output_path=output_dir,
        legend_label=legend_label,
        spatial_agg=spatial_agg,
        vmin=global_vmin,
        vmax=global_vmax,
    )

    plot_time_slices_by_bioregion_overlay(
        index_name=index_name,
        data_dir=input_dir,
        shapefile_path=shape_path,
        towns_csv_path=towns_path,
        index_variable=index_var,
        time_labels=time_labels,
        scenario_order=scenarios,
        cmap=cmap,
        output_path=output_dir,
        legend_label=legend_label,
        vmin=global_vmin,
        vmax=global_vmax,
    )