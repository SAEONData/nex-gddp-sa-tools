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

def find_root(start: Path, marker="plot_config.yml") -> Path:
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Cannot find '{marker}' upward from {start}")

SCRIPT   = Path(__file__).resolve()
ROOT     = find_root(SCRIPT)
CFG_PATH = ROOT / "plot_config.yml"
SRC_DIR  = ROOT / "src"

# 2. Add src to path
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 3. Load YAML config
with CFG_PATH.open() as f:
    cfg = yaml.safe_load(f)

# 4. Import the plotting function
from plot_index_by_bioregion import plot_time_slices_by_bioregion

# 5. Shared paths
shape_path = (ROOT / cfg["paths"]["shapefile"]).resolve()
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
    index_var  = entry.get("variable", f"max_{index_name}")
    input_dir  = (ROOT / entry.get("data_dir", f"data/outputs/{index_name}")).resolve()
    cmap       = entry.get("cmap", "YlOrBr")

    print(f"\n📊 Plotting {index_name.upper()} → variable={index_var}, input={input_dir}")
    
    legend_label = entry.get("legend_label", f"{index_var.replace('_', ' ').title()} (units)")
    
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
        legend_label=legend_label  # NEW
    )
    
#   plot_time_slices_by_bioregion(
#       index_name=index_name,
#       data_dir=input_dir,
#       shapefile_path=shape_path,
#       towns_csv_path=towns_path,
#       index_variable=index_var,
#       time_labels=time_labels,
#       scenario_order=scenarios,
#       cmap=cmap,
#       output_path=output_dir
#   )