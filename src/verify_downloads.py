#!/usr/bin/env python3
"""
verify_downloads.py
---------------------------------------------------------------
Verify completeness of downloaded CMIP6 NetCDF files by model,
experiment, and variable based on the configuration YAML file.
Generates summary and detailed CSV reports.
"""

import yaml
from pathlib import Path
import glob
import pandas as pd


def count_expected_files(start, end):
    """Returns the number of expected files for a given time range (inclusive)."""
    return end - start + 1


def main():
    print("🔍 Starting download verification...")

    # ─── Load Configuration ───
    cfg_path = Path(__file__).resolve().parents[1] / "download_config.yml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"❌ Config file not found at {cfg_path}.\n"
            "➡️  Please copy 'download_config_template.yml' to 'download_config.yml' and edit it."
        )

    with cfg_path.open() as fh:
        cfg = yaml.safe_load(fh)

    # ─── Config Parameters ───
    selected_models = cfg["models"]["select"]
    experiments = cfg["experiments"]["select"]
    time_ranges = cfg["experiments"]["time_ranges"]
    variables = cfg["variables"]["daily"]
    expected_counts = {
        exp: count_expected_files(*time_ranges[exp]) for exp in experiments
    }

    root = cfg_path.parent
    data_dir = root / "data"
    output_dir = data_dir / "outputs"
    output_dir.mkdir(exist_ok=True)

    # ─── Initialize ───
    records = []
    chart_data = []
    per_exp_data = {model: {exp: {"found": 0, "expected": 0} for exp in experiments}
                    for model in selected_models}
    grand_total_files = 0

    # ─── Scan Files ───
    for model in selected_models:
        for var in variables:
            row = {"Model": model, "Variable": var}
            total_found = total_expected = 0

            for exp in experiments:
                expected = expected_counts[exp]
                path = data_dir / var / model / exp
                files = glob.glob(str(path / f"{var}_*.nc"))
                found = len(files)

                total_expected += expected
                total_found += found
                grand_total_files += found

                per_exp_data[model][exp]["found"] += found
                per_exp_data[model][exp]["expected"] += expected

                if found == expected:
                    status = "✅"
                elif found < expected:
                    status = f"⚠️ {found}/{expected}"
                else:
                    status = f"🔴 {found}/{expected}"

                row[f"{exp} ({expected})"] = status

            row["Total"] = f"{total_found}/{total_expected}"
            row["Overall"] = (
                "✅" if total_found == total_expected
                else "🔴" if total_found > total_expected
                else "⚠️"
            )

            records.append(row)

            percent_complete = (total_found / total_expected) * 100 if total_expected > 0 else 0
            chart_data.append({
                "Model": model,
                "Variable": var,
                "Percent Complete": round(percent_complete, 1)
            })

    # ─── Save Detailed Report ───
    df = pd.DataFrame(records)
    df.to_csv(output_dir / "download_verification_details.csv", index=False)

    # ─── Scenario Summary Report ───
    scenario_records = []
    for model, exp_data in per_exp_data.items():
        for exp, stats in exp_data.items():
            found, expected = stats["found"], stats["expected"]
            percent = (found / expected) * 100 if expected > 0 else 0
            scenario_records.append({
                "Model": model,
                "Scenario": exp,
                "Percent Complete": round(percent, 1)
            })

    scenario_df = pd.DataFrame(scenario_records)
    scenario_pivot = scenario_df.pivot(index="Model", columns="Scenario", values="Percent Complete").fillna(0)
    scenario_pivot.to_csv(output_dir / "download_verification_by_scenario.csv")

    # ─── Output Summary ───
    print("\n📋 Download Summary by Variable\n")
    print(df.to_string(index=False))
    print(f"\n💾 Saved: {output_dir / 'download_verification_details.csv'}")
    print(f"💾 Saved: {output_dir / 'download_verification_by_scenario.csv'}")

    total_found = sum(exp["found"] for m in per_exp_data.values() for exp in m.values())
    total_expected = sum(exp["expected"] for m in per_exp_data.values() for exp in m.values())
    overall_pct = (total_found / total_expected * 100) if total_expected > 0 else 0

    print("\n📁 Total Files Downloaded:", total_found)
    print("📦 Total Expected Files:", total_expected)
    print(f"✅ Overall Completion: {overall_pct:.2f}%")


if __name__ == "__main__":
    main()