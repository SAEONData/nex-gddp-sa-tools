#!/usr/bin/env python3
"""
normalize_all_indices.py
---------------------------------------------------
Normalizes all index outputs across scenarios,
based on the config and folder structure defined
relative to the project root.
"""

import sys
from pathlib import Path
import yaml
from normalize_outputs import normalize_index_outputs

# 1. Locate project root by config file
def find_root(start: Path, marker: str = "climate_indices_config.yml") -> Path:
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Cannot find '{marker}' upward from {start}")

SCRIPT = Path(__file__).resolve()
ROOT   = find_root(SCRIPT)
CFG_PATH = ROOT / "climate_indices_config.yml"
OUTPUT_DIR = ROOT / "data" / "outputs"

# 2. Load config to get list of indices
with CFG_PATH.open() as fh:
    cfg = yaml.safe_load(fh)

indices_to_normalize = cfg.get("run_indices", [])

if not indices_to_normalize:
    print("⚠️  No indices specified in 'run_indices'. Nothing to normalize.")
    sys.exit(0)

# 3. Normalize each index
for idx in indices_to_normalize:
    try:
        print(f"\n📊 Normalizing index: {idx}")
        normalize_index_outputs(idx, output_dir=str(OUTPUT_DIR))
    except Exception as e:
        print(f"❌ Error normalizing {idx}: {e}")