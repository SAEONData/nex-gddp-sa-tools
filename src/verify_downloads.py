#!/usr/bin/env python3

import yaml
from pathlib import Path
import glob
import pandas as pd
import matplotlib.pyplot as plt


def count_expected_files(start, end):
    return end - start  # assuming 1 file per year


def main():
    # ───── Locate config file ─────
    CFG_PATH = Path(__file__).resolve().parents[1] / "download_config.yml"
    if not CFG_PATH.exists():
        raise FileNotFoundError(
            f"Config file {CFG_PATH} not found. "
            "Copy download_config_template.yml to download_config.yml and edit it first."
        )

    with CFG_PATH.open() as fh:
        cfg = yaml.safe_load(fh)

    # ───── Config ─────
    selected_models = cfg["models"]["select"]
    experiments     = cfg["experiments"]["select"]
    time_ranges     = cfg["experiments"]["time_ranges"]
    variables       = cfg["variables"]["daily"]
    expected_counts = {exp: count_expected_files(*time_ranges[exp]) for exp in experiments}

    ROOT = CFG_PATH.parent
    DATA_DIR = ROOT / "data"
    OUTPUT_DIR = ROOT / "data" / "outputs"
    OUTPUT_DIR.mkdir(exist_ok=True)

    # ───── Scan and report ─────
    records = []
    chart_data = []
    per_exp_data = {}  # model → exp → [found, expected]

    for model in selected_models:
        if model not in per_exp_data:
            per_exp_data[model] = {exp: {"found": 0, "expected": 0} for exp in experiments}

        for var in variables:
            row = {"Model": model, "Variable": var}
            total_expected, total_found = 0, 0

            for exp in experiments:
                expected = expected_counts[exp]
                path = DATA_DIR / var / model / exp
                files = glob.glob(str(path / f"{var}_*.nc"))
                found = len(files)

                total_expected += expected
                total_found += found

                per_exp_data[model][exp]["found"] += found
                per_exp_data[model][exp]["expected"] += expected

                status = "✅" if found == expected else f"⚠️ {found}/{expected}"
                row[f"{exp} ({expected})"] = status

            row["Total"] = f"{total_found}/{total_expected}"
            row["Overall"] = "✅" if total_found == total_expected else "⚠️"
            records.append(row)

            percent = (total_found / total_expected) * 100 if total_expected > 0 else 0
            chart_data.append({
                "Model": model,
                "Variable": var,
                "Percent Complete": percent
            })

    # ───── Save variable-level CSV ─────
    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "download_verification_details.csv", index=False)


    # ───── Scenario-based summary ─────
    scenario_records = []
    for model in per_exp_data:
        for exp in experiments:
            found = per_exp_data[model][exp]["found"]
            expected = per_exp_data[model][exp]["expected"]
            percent = (found / expected) * 100 if expected > 0 else 0
            scenario_records.append({
                "Model": model,
                "Scenario": exp,
                "Percent Complete": round(percent, 1)
            })

    scenario_df = pd.DataFrame(scenario_records)
    scenario_pivot = scenario_df.pivot(index="Model", columns="Scenario", values="Percent Complete").fillna(0)
    scenario_pivot.to_csv(OUTPUT_DIR / "download_verification_by_scenario.csv")


    # ───── Output ─────
    print("\nDownload Verification Summary per Variable\n")
    print(df.to_string(index=False))
    print(f"\nCSV saved to: {OUTPUT_DIR / 'download_verification_summary.csv'}")
    print(f"Scenario CSV saved to: {OUTPUT_DIR / 'download_verification_by_scenario.csv'}")
    

if __name__ == "__main__":
    main()