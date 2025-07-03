#!/usr/bin/env python3
import subprocess
import sys
import os
import yaml

# get the directory where this script lives
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# absolute path to the catalog CLI
CATALOG = os.path.join(BASE_DIR, "nex_gddp_catalog.py")

def get_models():
    out = subprocess.check_output(
        [sys.executable, CATALOG, "models"],
        text=True
    )
    return out.strip().split()

def get_info(model):
    out = subprocess.check_output(
        [sys.executable, CATALOG, "info", model],
        text=True
    )
    data = yaml.safe_load(out)
    # Post-process: ensure each ensemble’s vars is a list
    for scenario, ensembles in data.items():
        for ens, vars_blob in ensembles.items():
            if isinstance(vars_blob, str):
                # split on commas and strip whitespace
                vars_list = [v.strip() for v in vars_blob.split(",") if v.strip()]
                data[scenario][ens] = vars_list
    return data

def main():
    models = get_models()

    # print Markdown table header
    print("| Model       | Scenario   | Ensemble   | Variables                                                   |")
    print("| ----------- | ---------- | ---------- | ------------------------------------------------------------ |")

    for model in models:
        info = get_info(model)
        for scenario, ensembles in info.items():
            for ens, vars_list in ensembles.items():
                vars_str = ", ".join(vars_list)
                print(f"| {model} | {scenario} | {ens} | {vars_str} |")

if __name__ == "__main__":
    try:
        import yaml
    except ImportError:
        print("Please install PyYAML: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    main()