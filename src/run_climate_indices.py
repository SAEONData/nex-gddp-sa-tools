#!/usr/bin/env python3
"""
run_climate_indices.py
---------------------------------------------------
Loads config and executes selected climate indices.
Each index should be implemented in its own module,
named <index>_biomes.py with a run(cfg) function.
"""

import sys
from pathlib import Path
import importlib
import yaml

# 1. Locate project root and config file
def find_root(start: Path, marker: str = "climate_indices_config.yml") -> Path:
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Cannot find '{marker}' upward from {start}")

SCRIPT   = Path(__file__).resolve()
ROOT     = find_root(SCRIPT)
CFG_PATH = ROOT / "climate_indices_config.yml"
SRC_DIR  = ROOT / "src" / "climate_indices"

# 2. Ensure src/climate_indices is importable
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 3. Load YAML config
with CFG_PATH.open() as fh:
    cfg = yaml.safe_load(fh)

indices_to_run = cfg.get("run_indices", [])

if not indices_to_run:
    print("No indices specified in 'run_indices'. Exiting.")
    sys.exit(0)

# 4. Dynamically import and run each index
for index in indices_to_run:
    module_name = f"{index}_biomes"  # e.g., "cdd_biomes"
    try:
        module = importlib.import_module(module_name)
        print(f"\n Running index: {index.upper()} → {module_name}.py")
        module.run(cfg)
    except ImportError as e:
        print(f"Could not import module '{module_name}': {e}")
    except Exception as e:
        print(f"Error running index '{index}': {e}")