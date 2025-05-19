#!/usr/bin/env python3
"""
nex_gddp_catalog.py
====================================================================
Explore the NASA NEX-GDDP-CMIP6 THREDDS catalogue from the command line.

Examples
--------
# 1. List every model
python src/nex_gddp_catalog.py models

# 2. Show experiments → runs → variables for one model
python src/nex_gddp_catalog.py info ACCESS-CM2

# 3. List variable codes for a specific run
python src/nex_gddp_catalog.py vars ACCESS-CM2 ssp585 r1i1p1f1

# 4. List all file names (incl. _v1.1 etc.) for one variable & year span
python src/nex_gddp_catalog.py files ACCESS-CM2 historical r1i1p1f1 pr
"""
from __future__ import annotations
import argparse, sys, requests, xml.etree.ElementTree as ET
from urllib.parse import urljoin
from typing import List, Set

BASE_URL = "https://ds.nccs.nasa.gov/thredds/catalog/AMES/NEX/GDDP-CMIP6/"
_NS = {"th": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"}


class NexGDDPCatalog:
    """Tiny wrapper around the THREDDS XML catalogue."""

    # ---- helpers ---------------------------------------------------- #
    def _fetch(self, url: str) -> ET.Element:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return ET.fromstring(r.text)

    def _refs(self, xml_root: ET.Element) -> List[str]:
        """Return <catalogRef> names (uses name or xlink:title)."""
        out = []
        for ref in xml_root.findall(".//th:catalogRef", _NS):
            attr = ref.get("name") or ref.get("{http://www.w3.org/1999/xlink}title")
            if attr:
                out.append(attr)
        return out

    # ---- public API ------------------------------------------------- #
    def models(self) -> List[str]:
        return sorted(self._refs(self._fetch(urljoin(BASE_URL, "catalog.xml"))))

    def experiments(self, model: str) -> List[str]:
        return sorted(self._refs(self._fetch(urljoin(BASE_URL, f"{model}/catalog.xml"))))

    def runs(self, model: str, experiment: str) -> List[str]:
        return sorted(
            self._refs(self._fetch(urljoin(BASE_URL, f"{model}/{experiment}/catalog.xml")))
        )

    def variables(self, model: str, experiment: str, run: str) -> Set[str]:
        """Return variable codes (pr, tasmax, …) — works for both catalogue layouts."""
        run_xml = self._fetch(urljoin(BASE_URL, f"{model}/{experiment}/{run}/catalog.xml"))

        # Case 1: variables appear as sub-catalogues
        vars_cat = set(self._refs(run_xml))
        if vars_cat:
            return vars_cat

        # Case 2: variables appear directly as <dataset> entries
        return {
            ds.get("name").split("_")[0]
            for ds in run_xml.findall(".//th:dataset", _NS)
        }

    def files(self, model: str, experiment: str, run: str, variable: str) -> List[str]:
        """Return every dataset file name (all versions) for model / experiment / run / var."""
        url = urljoin(BASE_URL, f"{model}/{experiment}/{run}/{variable}/catalog.xml")
        root = self._fetch(url)
        return [ds.get("name") for ds in root.findall(".//th:dataset", _NS)]


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main():
    cat = NexGDDPCatalog()
    p = argparse.ArgumentParser(description="Explore NEX-GDDP-CMIP6 catalogue")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="List all CMIP6 models")

    xp = sub.add_parser("info", help="List experiments, runs, vars for a model")
    xp.add_argument("model")

    vr = sub.add_parser("vars", help="List variable codes for model / exp / run")
    vr.add_argument("model")
    vr.add_argument("experiment")
    vr.add_argument("run")

    rn = sub.add_parser("runs", help="List ensemble runs for model / experiment")
    rn.add_argument("model")
    rn.add_argument("experiment")

    fl = sub.add_parser("files", help="List file names for model / exp / run / var")
    fl.add_argument("model")
    fl.add_argument("experiment")
    fl.add_argument("run")
    fl.add_argument("variable")

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

    elif args.cmd == "runs":
        print("\n".join(cat.runs(args.model, args.experiment)))

    elif args.cmd == "files":
        files = cat.files(args.model, args.experiment, args.run, args.variable)
        print("\n".join(files) or "No files found")


if __name__ == "__main__":
    main()