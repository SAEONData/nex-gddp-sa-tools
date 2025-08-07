import os
import xarray as xr
import numpy as np
from pathlib import Path

def normalize_index_outputs(index_name: str, output_dir: str = "data/outputs"):
    """
    Normalize index output data across all scenarios for a given index.

    Args:
        index_name (str): Name of the index (e.g. 'cdd')
        output_dir (str): Directory containing per-index folders
                          e.g. data/outputs/cdd/historical.nc, ssp126.nc, etc.
    """
    index_dir = Path(output_dir) / index_name
    if not index_dir.exists():
        raise FileNotFoundError(f"No output found at {index_dir}")

    scenario_files = list(index_dir.glob("*.nc"))
    if not scenario_files:
        raise FileNotFoundError(f"No NetCDF files found in {index_dir}")

    print(f"\n🔍 Normalizing {index_name.upper()} across {len(scenario_files)} scenarios...")

    data_arrays = {}
    for path in scenario_files:
        scenario = path.stem
        try:
            ds = xr.open_dataset(path)

            # Try common variable name patterns
            candidate_vars = [
                index_name,
                f"{index_name}_ensemble_mean",
                f"max_{index_name}"
            ]

            for var in candidate_vars:
                if var in ds:
                    data_var = ds[var]
                    print(f"✅ Using variable '{var}' from {path.name}")
                    break
            else:
                # Fallback: use only variable if one exists
                if len(ds.data_vars) == 1:
                    var = list(ds.data_vars)[0]
                    data_var = ds[var]
                    print(f"⚠️ Using only variable found: '{var}' in {path.name}")
                else:
                    raise KeyError(f"No matching variable found in {path.name}")

            data_arrays[scenario] = data_var

        except Exception as e:
            print(f"⚠️ Skipping {path.name}: {e}")

    if not data_arrays:
        raise ValueError(f"No valid data arrays found for index '{index_name}'.")

    # 2. Stack all scenario arrays into one DataArray
    combined = xr.concat(data_arrays.values(), dim="scenario")
    combined["scenario"] = list(data_arrays.keys())

    # 3. Min-max normalization across scenarios
    normalized = (combined - combined.min(dim="scenario")) / (combined.max(dim="scenario") - combined.min(dim="scenario"))
    normalized.name = f"{index_name}_normalized"

    # 4. Save normalized output per scenario
    out_dir = index_dir / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)

    for scenario in normalized.scenario.values:
        norm_ds = normalized.sel(scenario=scenario).to_dataset(name=normalized.name)
        out_file = out_dir / f"{scenario}.nc"
        norm_ds.to_netcdf(out_file)
        print(f"💾 Saved normalized {index_name.upper()} to: {out_file}")

    return normalized