from pathlib import Path
import xarray as xr
import geopandas as gpd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm, Normalize
import matplotlib.transforms as mtransforms
import regionmask


def normalize_label(label: str) -> str:
    return (
        label.lower()
        .replace("–", "-").replace("—", "-")
        .replace(" ", "_").replace("(", "").replace(")", "")
    )


def _to_epsg4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf.to_crs("EPSG:4326")


def _find_lon_lat(da: xr.DataArray):
    lon_candidates = ["lon", "longitude", "x"]
    lat_candidates = ["lat", "latitude", "y"]
    lon_name = next((n for n in lon_candidates if n in da.coords), None)
    lat_name = next((n for n in lat_candidates if n in da.coords), None)
    if lon_name is None or lat_name is None:
        raise ValueError(f"Could not find lon/lat coords in: {list(da.coords)}")
    return lon_name, lat_name


def _normalize_lon(da: xr.DataArray) -> xr.DataArray:
    lon_name, _ = _find_lon_lat(da)
    lon = da[lon_name]
    if lon.min() >= 0 and lon.max() > 180:
        new_lon = ((lon + 180) % 360) - 180
        da = da.assign_coords({lon_name: new_lon}).sortby(lon_name)
    return da


def _build_mask_from_gdf(gdf: gpd.GeoDataFrame, ref_da: xr.DataArray) -> xr.DataArray:
    geom = gdf.dissolve().geometry.unary_union
    regs = regionmask.Regions([geom])
    lon_name, lat_name = _find_lon_lat(ref_da)
    lon_1d = ref_da[lon_name].values
    lat_1d = ref_da[lat_name].values
    mask2d = regs.mask(lon_1d, lat_1d)  # (lat, lon)
    mask_da = xr.DataArray(
        mask2d.notnull(), dims=("lat", "lon"), coords={"lat": lat_1d, "lon": lon_1d}
    )
    return mask_da.reindex_like(ref_da, method=None, copy=False).astype(bool)


def _timedelta_to_days_float(da: xr.DataArray) -> xr.DataArray:
    """If DataArray is timedelta64, convert to float days and strip CF encoding."""
    import numpy as np
    if np.issubdtype(da.dtype, np.timedelta64):
        da = (da / np.timedelta64(1, "D")).astype("float32")
        try:
            da.encoding.clear()
        except Exception:
            pass
        attrs = dict(da.attrs)
        attrs.pop("units", None)
        da.attrs = attrs
        da.attrs["units"] = "days"
    return da


def plot_time_slices_by_municipality_overlay(
    index_name,
    data_dir,
    shapefile_path,
    towns_csv_path,  # kept for signature compatibility; not used
    index_variable,
    time_labels,
    scenario_order,
    cmap="RdBu_r",
    output_path=None,
    legend_label=None,
    vmin=None,
    vmax=None,
    anomaly=True,
    show_extrema_labels=False,   # ← toggle value labels on the colorbar
):
    data_dir = Path(data_dir)
    all_files = list(data_dir.glob("*.nc"))

    municipalities = _to_epsg4326(gpd.read_file(shapefile_path))

    prefix_type = "anomaly" if anomaly else "ensemble_mean"

    plot_data = {}
    all_values = []

    mask_cache = None
    mask_sig = None

    # Load & mask data (per panel)
    for scenario in scenario_order:
        for label in time_labels:
            label_slug = normalize_label(label)
            expected_prefix = f"{index_name}_{prefix_type}_{scenario}_{label_slug}"
            match = next((f for f in all_files if expected_prefix in f.name), None)
            if not match:
                print(f"⚠️ No file found for: {scenario} / {label}")
                continue

            ds = xr.open_dataset(match, decode_timedelta=False)

            # pick variable
            if index_variable in ds:
                da = ds[index_variable]
            elif len(ds.data_vars) == 1:
                da = next(iter(ds.data_vars.values()))
            else:
                print(f"⚠️ Variable '{index_variable}' not found in {match.name}")
                continue

            da = _timedelta_to_days_float(da)
            da = _normalize_lon(da)

            lon_name, lat_name = _find_lon_lat(da)
            sig = (tuple(da.sizes.get(d) for d in da.dims), lon_name, lat_name)

            if (mask_cache is None) or (sig != mask_sig):
                mask_cache = _build_mask_from_gdf(municipalities, da)
                mask_sig = sig

            da_masked = da.where(mask_cache)
            plot_data[(scenario, label)] = da_masked
            all_values.extend(da_masked.values.ravel())

    all_values = np.asarray(all_values)
    if not all_values.size or np.isnan(all_values).all():
        raise ValueError("❌ No valid (in-boundary) data found for plotting.")

    # Actual (masked) extrema for markers
    actual_min = float(np.nanmin(all_values))
    actual_max = float(np.nanmax(all_values))

    # ---- Bounds & norm (robust + symmetric for anomalies) ----
    if vmin is None or vmax is None:
        data_min = actual_min
        data_max = actual_max
    else:
        data_min, data_max = float(vmin), float(vmax)

    if anomaly:
        max_abs = max(abs(data_min), abs(data_max), 1e-6)
        global_vmin, global_vmax = -max_abs, max_abs
    else:
        if data_min == data_max:
            eps = 1e-6 * (1.0 if data_min == 0 else abs(data_min))
            global_vmin, global_vmax = data_min - eps, data_max + eps
        else:
            global_vmin, global_vmax = data_min, data_max

    if anomaly and (global_vmin < 0 < global_vmax):
        norm = TwoSlopeNorm(vmin=global_vmin, vcenter=0, vmax=global_vmax)
    else:
        norm = Normalize(vmin=global_vmin, vmax=global_vmax)

    # ---- Plot grid ----
    n_rows = len(scenario_order)
    n_cols = len(time_labels)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(6 * n_cols, 5 * n_rows), constrained_layout=True)
    axes = np.atleast_2d(axes)

    last_mappable = None
    for i, scenario in enumerate(scenario_order):
        for j, label in enumerate(time_labels):
            ax = axes[i, j]
            da = plot_data.get((scenario, label))
            if da is None:
                ax.set_title(f"{scenario.upper()} — {label}\n(no data)", fontsize=16)
                ax.set_axis_off()
                continue

            mappable = da.plot(ax=ax, cmap=cmap, norm=norm, add_colorbar=False)
            last_mappable = mappable

            municipalities.boundary.plot(ax=ax, edgecolor="black", linewidth=0.5)
            ax.set_title(f"{scenario.upper()} — {label}", fontsize=16)
            ax.set_axis_off()

    # ---- Shared colorbar with evenly spaced ticks + extrema markers ----
    if last_mappable is not None:
        cbar = fig.colorbar(last_mappable, ax=axes.ravel().tolist(), shrink=0.85)
        cbar.set_label(
            legend_label or f"{index_variable.replace('_', ' ').title()} (units)",
            fontsize=18
        )

        n_ticks = 6
        ticks = np.linspace(global_vmin, global_vmax, n_ticks)
        if anomaly and (global_vmin < 0 < global_vmax):
            zero_idx = np.argmin(np.abs(ticks))
            ticks[zero_idx] = 0.0
        units = getattr(da, "units", "").lower() if 'da' in locals() else ""
        if "day" in units:
            labels = [f"{t:.0f}" for t in ticks]  # always integer labels for days
        else:
            labels = [f"{t:.1f}" for t in ticks]  # keep decimal for other units
        ticks[0], ticks[-1] = global_vmin, global_vmax
        cbar.set_ticks(ticks)
        cbar.set_ticklabels(labels)
        cbar.ax.tick_params(labelsize=16)

        # Draw thin lines at actual min/max
        trans = mtransforms.blended_transform_factory(cbar.ax.transAxes, cbar.ax.transData)
        for val in (actual_min, actual_max):
            if global_vmin <= val <= global_vmax:
                cbar.ax.hlines(val, 0.1, 0.9, transform=trans, linewidth=1.2)

        if show_extrema_labels:
            cbar.ax.text(0.95, actual_min, f"{actual_min:.1f}", va="center", ha="left", transform=trans, fontsize=8)
            cbar.ax.text(0.95, actual_max, f"{actual_max:.1f}", va="center", ha="left", transform=trans, fontsize=8)

    # ---- Save/show ----
    if output_path:
        suffix = "anomaly_overlay_municipalities.png" if anomaly else "ensemble_overlay_municipalities.png"
        plot_file = Path(output_path) / f"{index_variable}_{suffix}"
        plot_file.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(plot_file, dpi=300, bbox_inches="tight")
        print(f"✅ Overlay plot saved to: {plot_file}")
    else:
        plt.show()