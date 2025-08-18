#!/usr/bin/env python3
"""
verify_downloads.py
---------------------------------------------------------------
Verify completeness and basic health of downloaded CMIP6 NetCDF files
by model, experiment, and variable based on download_config.yml.

Outputs:
- data/outputs/download_health_files.csv           (per-file health)
- data/outputs/download_verification_details.csv   (table by model×var)
- data/outputs/download_verification_by_scenario.csv (pivot by scenario)
"""

from pathlib import Path
import glob
import re
import os

import yaml
import pandas as pd
import xarray as xr

YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

def count_expected_files(start, end):
    return end - start + 1

def year_from_name(p: Path):
    m = YEAR_RE.search(p.name)
    return int(m.group()) if m else None

def is_healthy_nc(p: Path, var_hint: str | None = None) -> tuple[bool, str]:
    """
    Basic file sanity:
    - file exists and > 1 KiB
    - can be opened by xarray (h5netcdf)
    - has lon/lat/time coords (time optional for some static files but GDDP daily has it)
    - contains the expected variable if var_hint provided
    Returns (ok, reason). reason="" if ok.
    """
    try:
        if not p.exists():
            return False, "missing_file"
        if os.path.getsize(p) < 1024:
            return False, "size_lt_1k"

        with xr.open_dataset(p, engine="h5netcdf", decode_times=False) as ds:
            # coords
            lon_ok = any(c in ds.coords for c in ("lon", "longitude", "x"))
            lat_ok = any(c in ds.coords for c in ("lat", "latitude", "y"))
            time_ok = ("time" in ds)
            if not lon_ok or not lat_ok:
                return False, "no_lon_lat"
            if not time_ok:
                return False, "no_time"

            # main variable
            if var_hint is not None:
                if var_hint not in ds.variables:
                    # sometimes variable is present but compressed differently – be strict here
                    return False, f"missing_var:{var_hint}"

        return True, ""
    except Exception as e:
        return False, f"xr_open_error:{type(e).__name__}"

def main():
    print("🔍 Starting download verification (with health checks)…")

    cfg_path = Path(__file__).resolve().parents[1] / "download_config.yml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"❌ Config file not found at {cfg_path}.\n"
            "➡️  Copy 'download_config_template.yml' to 'download_config.yml' and edit it."
        )

    with cfg_path.open() as fh:
        cfg = yaml.safe_load(fh)

    selected_models = cfg["models"]["select"]
    experiments = cfg["experiments"]["select"]
    time_ranges = cfg["experiments"]["time_ranges"]
    variables = cfg["variables"]["daily"]
    expected_counts = {exp: count_expected_files(*time_ranges[exp]) for exp in experiments}

    root = cfg_path.parent
    data_dir = root / "data"
    output_dir = data_dir / "outputs"
    output_dir.mkdir(exist_ok=True)

    # Aggregates
    table_rows = []
    scenario_agg = {m: {e: {"healthy": 0, "expected": expected_counts[e]} for e in experiments}
                    for m in selected_models}
    files_health_rows = []

    # Scan
    for model in selected_models:
        for var in variables:
            row = {"Model": model, "Variable": var}
            total_expected = sum(expected_counts[e] for e in experiments)
            total_healthy = 0

            for exp in experiments:
                expected = expected_counts[exp]
                path = data_dir / var / model / exp
                files = sorted(Path(p) for p in glob.glob(str(path / f"{var}_*.nc")))

                # Health-check each file
                healthy_files = []
                years_seen = set()
                for fp in files:
                    ok, reason = is_healthy_nc(fp, var_hint=var)
                    yr = year_from_name(fp)
                    if ok:
                        healthy_files.append(fp)
                        if yr is not None:
                            years_seen.add(yr)
                    files_health_rows.append({
                        "model": model,
                        "variable": var,
                        "experiment": exp,
                        "file": str(fp.relative_to(root)) if fp.exists() else str(fp),
                        "year": yr,
                        "healthy": ok,
                        "reason": reason,
                    })

                healthy_count = len(healthy_files)
                total_healthy += healthy_count
                scenario_agg[model][exp]["healthy"] += healthy_count

                # Year coverage diagnostics
                y0, y1 = time_ranges[exp]
                expected_years = set(range(y0, y1 + 1))
                missing_years = sorted(expected_years - years_seen)
                extra_years = sorted(years_seen - expected_years)

                if healthy_count == expected:
                    status = "✅"
                elif healthy_count < expected:
                    status = f"⚠️ {healthy_count}/{expected}"
                else:
                    status = f"🔴 {healthy_count}/{expected}"

                detail = status
                if missing_years:
                    # keep short; full detail is in CSV
                    detail += f" (missing {len(missing_years)}y)"
                if extra_years:
                    detail += f" (+{len(extra_years)} extra)"

                row[f"{exp} ({expected})"] = detail

            row["Total"] = f"{total_healthy}/{total_expected}"
            row["Overall"] = (
                "✅" if total_healthy == total_expected
                else "🔴" if total_healthy > total_expected
                else "⚠️"
            )
            table_rows.append(row)

    # Save per-file health
    health_df = pd.DataFrame(files_health_rows)
    health_csv = output_dir / "download_health_files.csv"
    health_df.to_csv(health_csv, index=False)

    # Detailed table
    details_df = pd.DataFrame(table_rows)
    details_csv = output_dir / "download_verification_details.csv"
    details_df.to_csv(details_csv, index=False)

    # Scenario summary (healthy %)
    scenario_records = []
    for model, exp_data in scenario_agg.items():
        for exp, stats in exp_data.items():
            healthy, expected = stats["healthy"], stats["expected"]
            pct = (healthy / expected * 100) if expected > 0 else 0.0
            scenario_records.append({"Model": model, "Scenario": exp, "Percent Complete": round(pct, 1)})
    scenario_df = pd.DataFrame(scenario_records)
    scenario_pivot = scenario_df.pivot(index="Model", columns="Scenario", values="Percent Complete").fillna(0.0)
    scen_csv = output_dir / "download_verification_by_scenario.csv"
    scenario_pivot.to_csv(scen_csv)

    # Console summary
    print("\n📋 Download Summary by Variable (healthy files only)\n")
    print(details_df.to_string(index=False))
    print(f"\n💾 Saved: {details_csv}")
    print(f"💾 Saved: {scen_csv}")
    print(f"💾 Saved: {health_csv}")

    total_healthy = scenario_df["Percent Complete"].mul(
        [expected_counts[s] for s in scenario_df["Scenario"]]
    ).sum() / 100.0
    total_expected = sum(expected_counts[s] for s in experiments) * len(selected_models) * len(variables)
    overall_pct = (total_healthy / total_expected * 100.0) if total_expected > 0 else 0.0

    print("\n📦 Total Expected Files:", total_expected)
    print("📁 Total Healthy Files :", int(total_healthy))
    print(f"✅ Overall Completion  : {overall_pct:.2f}%")

if __name__ == "__main__":
    main()