#!/Users/privateprivate/SARVA_ws/bin/python

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
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"↳ {dest.name}")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=2**20):
                fh.write(chunk)
                
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
    
    for year in range(start, end + 1):
        url = (BASE + params).format(
            model=model, exp=experiment, run=run, var=variable, year=year
        )
        dest = out_root / variable / model / experiment / f"{variable}_{year}.nc"
        download(url, dest)
                
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
        url = (BASE + PARAMS).format(
            model=args.model,
            exp=args.experiment,
            run=args.run,
            var=args.variable,
            year=year,
        )
        dest = out_root / f"{args.variable}_{year}.nc"
        download(url, dest)
        files.append(dest)
        
    # Optional: preview merged dataset
    ds = xr.open_mfdataset(files, concat_dim="time")
    print("\nDataset loaded →", ds)
    
if __name__ == "__main__":
    main()
    