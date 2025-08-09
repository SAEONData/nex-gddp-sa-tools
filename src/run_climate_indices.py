#!/usr/bin/env python3
"""
run_climate_indices.py
---------------------------------------------------
Loads config and executes selected climate indices.
Each index should be implemented in its own module,
named <index>_compute.py with a run(cfg) function.

After running the indices, also computes anomaly maps
by subtracting the historical baseline.
"""

import sys
from pathlib import Path
import importlib
import yaml
import xarray as xr
import numpy as np

print(f"Using Python interpreter: {sys.executable}")

def find_root(start: Path, marker: str = "climate_indices_config.yml") -> Path:
    """Locate project root by walking upward until marker is found."""
    here = start.resolve()
    for parent in [here, *here.parents]:
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Cannot find '{marker}' upward from {start}")

def label_slug(s: str) -> str:
    return (
        s.lower()
         .replace(" ", "_")
         .replace("(", "")
         .replace(")", "")
         .replace("–", "-")
    )

# 1) Resolve paths
SCRIPT = Path(__file__).resolve()
ROOT = find_root(SCRIPT)
CFG_PATH = ROOT / "climate_indices_config.yml"
SRC_DIR = ROOT / "src" / "climate_indices"

# 2) Ensure src/climate_indices importable
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 3) Load YAML config
with CFG_PATH.open() as fh:
    cfg = yaml.safe_load(fh)

indices_to_run = cfg.get("run_indices", [])
if not indices_to_run:
    print("No indices specified in 'run_indices'. Exiting.")
    sys.exit(0)

# 4) Run index modules
for index in indices_to_run:
    module_name = f"{index}_compute"
    try:
        module = importlib.import_module(module_name)
        print(f"\n▶️  Running index: {index.upper()} → {module_name}.py")
        module.run(cfg)  # modules expose run(cfg)
    except ImportError as e:
        print(f"Could not import module '{module_name}': {e}")
    except Exception as e:
        print(f"Error running index '{index}': {e}")

# 5) Compute anomalies against historical baseline
print("\n📉 Computing anomalies relative to baseline...")
time_slices = cfg.get("time_slices", {})
historical_label = "Baseline (1995–2014)"
hist_slug = label_slug(historical_label)

for index in indices_to_run:
    output_dir = ROOT / "data" / "outputs" / index
    hist_file = output_dir / f"{index}_ensemble_mean_historical_{hist_slug}.nc"
    if not hist_file.exists():
        print(f"⚠️ Baseline file missing for {index} ({hist_file.name}). Skipping anomaly computation.")
        continue

    # Load baseline
    try:
        hist_ds = xr.open_dataset(hist_file, decode_timedelta=False)
    except Exception as e:
        print(f"❌ Failed to open baseline for {index}: {e}")
        continue

    # Identify primary variable (first data_var)
    try:
        hist_var = next(iter(hist_ds.data_vars.keys()))
        hist_vals = hist_ds[hist_var]
    except Exception as e:
        print(f"❌ Failed to extract baseline variable for {index}: {e}")
        continue

    for scenario in cfg.get("experiments", {}).get("select", []):
        if scenario == "historical":
            continue

        for slice_name, (start, end) in time_slices.items():
            if slice_name.startswith("Baseline"):
                continue

            slice_slug = label_slug(slice_name)
            future_file = output_dir / f"{index}_ensemble_mean_{scenario}_{slice_slug}.nc"
            if not future_file.exists():
                print(f"   ⚠️ Missing future file for {index} / {scenario} / {slice_name} ({future_file.name})")
                continue

            try:
                fut_ds  = xr.open_dataset(future_file, decode_timedelta=False)
                fut_var = next(iter(fut_ds.data_vars.keys()))
                fut_vals = fut_ds[fut_var]

                # Align grids if needed (simple reindex_like; consider more robust regridding if mismatched)
                if (set(hist_vals.dims) != set(fut_vals.dims)) or any(
                    hist_vals.sizes.get(dim) != fut_vals.sizes.get(dim) for dim in fut_vals.dims if dim in hist_vals.dims
                ):
                    try:
                        fut_vals = fut_vals.reindex_like(hist_vals, method=None)
                    except Exception:
                        pass  # If they already align, or reindex not applicable

                # Compute anomaly
                anomaly = fut_vals - hist_vals

                # ---- Normalize anomaly to avoid CF-timedelta encoding issues ----
                # If timedelta, convert to float days and drop CF encoding.
                if np.issubdtype(anomaly.dtype, np.timedelta64):
                    anomaly = (anomaly / np.timedelta64(1, "D")).astype("float32")
                    unit = "days"
                else:
                    unit = hist_vals.attrs.get("units", "")

                anomaly = anomaly.copy()
                # Clear any serialization hints that would clash with attrs
                try:
                    anomaly.encoding.clear()
                except Exception:
                    pass
                attrs = dict(anomaly.attrs)
                attrs.pop("units", None)
                anomaly.attrs = attrs
                anomaly.attrs["units"] = unit

                out_ds = xr.Dataset(
                    {f"{fut_var}_anomaly": anomaly},
                    attrs={
                        "title": f"{index.upper()} Anomaly: {scenario} – {slice_name}",
                        "description": (
                            f"Difference between {scenario} / {slice_name} "
                            f"and historical baseline (1995–2014)"
                        ),
                        "units": unit,
                        "created_by": "run_climate_indices.py",
                    },
                )

                out_path = output_dir / f"{index}_anomaly_{scenario}_{slice_slug}.nc"
                out_ds.to_netcdf(out_path)
                print(f"   ✅ Saved anomaly → {out_path.name}")

            except Exception as e:
                print(f"   ❌ Failed to compute anomaly for {index} / {scenario} / {slice_name}: {e}")