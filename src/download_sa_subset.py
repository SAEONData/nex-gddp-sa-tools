#!/usr/bin/env python3
"""download_sa_subset.py
Download a South‑Africa spatial subset (0.25° grid, lat −35‒−21°, lon 16‒33°)
from NASA NEX‑GDDP‑CMIP6 via THREDDS NetCDF Subset Service.

Example
-------
python download_sa_subset.py \
    --model ACCESS-CM2 \
    --experiment historical \
    --variable pr \
    --start 2010 --end 2014
"""
from pathlib import Path
import argparse
import requests
import xarray as xr

# South‑Africa bounding box (WGS84)
BBOX = dict(north=-21, south=-35, west=16, east=33)

BASE = (
    "https://ds.nccs.nasa.gov/thredds/ncss/grid/AMES/NEX/GDDP-CMIP6/"
    "{model}/{exp}/{run}/{var}/{var}_day_{model}_{exp}_{run}_gn_{year}.nc"
)

PARAMS = (
    "?var={var}&north={north}&west={west}&east={east}&south={south}"
    "&horizStride=1"
    "&time_start={year}-01-01T12:00:00Z"
    "&time_end={year}-12-31T12:00:00Z"
    "&accept=netcdf4&addLatLon=true"
).format(**BBOX, var="{var}", year="{year}")

def download(url: str, dest: Path):
    """
    Attempts to download a file from the given URL and save it to dest.
    """
    print(f"↳ Attempting: {url}")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=2**20):
                fh.write(chunk)
    print(f"✓ Saved to: {dest}")

# ----------------------------------------------------------------------
# Re-usable function: other scripts (e.g. run_downloads.py) can call this
# ----------------------------------------------------------------------
def download_sa_bbox(
    model: str,
    experiment: str,
    variable: str,
    start: int,
    end: int,
    *,
    run: str = "r1i1p1f1",
    bbox: dict = None,
    stride: int = 1,
    out_root: Path = Path("data"),
):
    """
    Download a South-Africa subset for one variable & year range.

    Parameters
    ----------
    model, experiment, variable : str
    start, end                  : int   inclusive years
    run                         : ensemble ID (default r1i1p1f1)
    bbox                        : dict(north, south, west, east)
    stride                      : horizStride (1 = native grid)
    out_root                    : root directory for NetCDFs
    """
    if bbox is None:
        bbox = BBOX

    params = PARAMS.replace("horizStride=1", f"horizStride={stride}")
    base_path = f"{variable}_day_{model}_{experiment}_{run}_gn_{{year}}.nc"

    for year in range(start, end + 1):
        # Prepare destination folder
        dest_folder = out_root / variable / model / experiment
        dest_folder.mkdir(parents=True, exist_ok=True)

        # Try _v1.1 first, then fallback
        versioned_filename = base_path.replace("{year}", str(year)).replace(".nc", "_v1.1.nc")
        fallback_filename = base_path.replace("{year}", str(year))

        for filename in [versioned_filename, fallback_filename]:
            url = (
                f"https://ds.nccs.nasa.gov/thredds/ncss/grid/AMES/NEX/GDDP-CMIP6/"
                f"{model}/{experiment}/{run}/{variable}/{filename}" + params.format(var=variable, year=year)
            )
            dest = dest_folder / filename
            try:
                download(url, dest)
                break  # Stop if successful
            except requests.HTTPError as e:
                print(f"{filename} not available: {e}")
            except Exception as e:
                print(f"Error: {e}")

# ----------------------------------------------------------------------
# Command-line execution
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Download South‑Africa subset from NEX‑GDDP‑CMIP6")
    parser.add_argument("--model", required=True, help="GCM name, e.g. ACCESS-CM2")
    parser.add_argument("--experiment", required=True, help="historical, ssp245, ssp585 …")
    parser.add_argument("--variable", required=True, help="pr, tasmax, tasmin, etc.")
    parser.add_argument("--run", default="r1i1p1f1", help="Ensemble run ID (default r1i1p1f1)")
    parser.add_argument("--start", type=int, required=True, help="First year (YYYY)")
    parser.add_argument("--end", type=int, required=True, help="Last year (YYYY, inclusive)")
    args = parser.parse_args()

    out_root = Path("data") / args.variable / args.model / args.experiment
    files = []

    for year in range(args.start, args.end + 1):
        # Try _v1.1 and fallback to base filename
        base_path = f"{args.variable}_day_{args.model}_{args.experiment}_{args.run}_gn_{year}.nc"
        versioned = base_path.replace(".nc", "_v1.1.nc")

        for filename in [versioned, base_path]:
            url = (
                f"https://ds.nccs.nasa.gov/thredds/ncss/grid/AMES/NEX/GDDP-CMIP6/"
                f"{args.model}/{args.experiment}/{args.run}/{args.variable}/{filename}" +
                PARAMS.format(var=args.variable, year=year)
            )
            dest = out_root / filename
            try:
                download(url, dest)
                files.append(dest)
                break
            except requests.HTTPError:
                continue

    # Optional: preview merged dataset
    if files:
        ds = xr.open_mfdataset(files, concat_dim="time")
        print("\nDataset loaded →", ds)

if __name__ == "__main__":
    main()