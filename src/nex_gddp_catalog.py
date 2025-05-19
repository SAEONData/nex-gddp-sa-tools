#!/usr/bin/env python3
"""
nex_gddp_catalog.py – explore the NASA NEX-GDDP-CMIP6 THREDDS catalogue.

Examples
--------
# List every model
python src/nex_gddp_catalog.py models

# Show experiments, runs, variables
python src/nex_gddp_catalog.py info ACCESS-CM2

# Show just the variable codes for one run
python src/nex_gddp_catalog.py vars ACCESS-CM2 ssp585 r1i1p1f1
"""
from __future__ import annotations
import argparse, sys, requests, xml.etree.ElementTree as ET
from urllib.parse import urljoin
from typing import List, Set

BASE_URL = "https://ds.nccs.nasa.gov/thredds/catalog/AMES/NEX/GDDP-CMIP6/"
_NS = {"th": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"}

class NexGDDPCatalog:
    """Tiny THREDDS wrapper."""

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _fetch(self, url: str) -> ET.Element:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return ET.fromstring(r.text)

    def _refs(self, xml_root: ET.Element) -> List[str]:
        """Return list of child catalogue names (use name or xlink:title)."""
        names = []
        for ref in xml_root.findall(".//th:catalogRef", _NS):
            attr = ref.get("name") or ref.get("{http://www.w3.org/1999/xlink}title")
            if attr:
                names.append(attr)
        return names

    # ------------------------------------------------------------------ #
    # Public methods                                                     #
    # ------------------------------------------------------------------ #
    def models(self) -> List[str]:
        return sorted(self._refs(self._fetch(urljoin(BASE_URL, "catalog.xml"))))

    def experiments(self, model: str) -> List[str]:
        return sorted(self._refs(self._fetch(urljoin(BASE_URL, f"{model}/catalog.xml"))))

    def runs(self, model: str, experiment: str) -> List[str]:
        return sorted(
            self._refs(self._fetch(urljoin(BASE_URL, f"{model}/{experiment}/catalog.xml")))
        )

    def variables(self, model: str, experiment: str, run: str) -> Set[str]:
        """Return variable codes (pr, tasmax, …) – works for both layouts."""
        run_xml = self._fetch(urljoin(BASE_URL, f"{model}/{experiment}/{run}/catalog.xml"))

        # 1) Variables as sub-catalogs
        vars_cat = set(self._refs(run_xml))
        if vars_cat:
            return vars_cat

        # 2) Variables as datasets
        vars_ds = {
            d.get("name").split("_")[0]
            for d in run_xml.findall(".//th:dataset", _NS)
        }
        return vars_ds

# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main():
    cat = NexGDDPCatalog()
    p = argparse.ArgumentParser(description="Explore NEX-GDDP-CMIP6 catalogue")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="List all CMIP6 models")

    i = sub.add_parser("info", help="List experiments, runs, vars for model")
    i.add_argument("model")

    v = sub.add_parser("vars", help="List vars for model + experiment + run")
    v.add_argument("model")
    v.add_argument("experiment")
    v.add_argument("run")

    args = p.parse_args()

    if args.cmd == "models":
        print("\n".join(cat.models()))

    elif args.cmd == "info":
        if args.model not in cat.models():
            sys.exit("Model not found.")
        for exp in cat.experiments(args.model):
            print(f"{exp}:")
            for run in cat.runs(args.model, exp):
                vars_ = ", ".join(sorted(cat.variables(args.model, exp, run))) or "—"
                print(f"  {run}: {vars_}")

    elif args.cmd == "vars":
        vars_ = cat.variables(args.model, args.experiment, args.run)
        print(", ".join(sorted(vars_)) if vars_ else "No variables found")

if __name__ == "__main__":
    main()