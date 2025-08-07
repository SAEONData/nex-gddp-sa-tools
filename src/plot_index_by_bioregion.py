def normalize_label(label):
    return (
        label.lower()
        .replace("–", "-")  # en dash
        .replace("—", "-")  # em dash
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )

def plot_time_slices_by_bioregion(
    index_name,
    data_dir,
    shapefile_path,
    towns_csv_path,
    index_variable,
    time_labels,
    scenario_order,
    cmap="YlOrBr",
    output_path=None,
    legend_label=None
):
    from pathlib import Path
    import xarray as xr
    import geopandas as gpd
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    import regionmask

    data_dir = Path(data_dir)
    all_files = list(data_dir.glob("*.nc"))

    # ───────── Load spatial data ─────────
    bioregions = gpd.read_file(shapefile_path).to_crs("EPSG:4326")
    region_mask = regionmask.Regions(
        outlines=bioregions.geometry,
        names=bioregions['Veg_Biome'],
        abbrevs=bioregions['Veg_Biome'],
        name="Bioregions"
    )

    towns_df = pd.read_csv(towns_csv_path, sep=';')
    towns_df.columns = towns_df.columns.str.strip()
    towns_gdf = gpd.GeoDataFrame(
        towns_df,
        geometry=gpd.points_from_xy(towns_df['lng'], towns_df['lat']),
        crs="EPSG:4326"
    )

    # ───────── Split scenarios ─────────
    historical_scenarios = [s for s in scenario_order if s == "historical"]
    scenario_runs = [s for s in scenario_order if s != "historical"]

    def collect_data(scenarios, labels):
        plot_data = {}
        all_values = []

        for scenario in scenarios:
            for label in labels:
                label_slug = normalize_label(label)
                expected_prefix = f"{index_name}_ensemble_mean_{scenario}_{label_slug}"
                print(expected_prefix)
                match = next((f for f in all_files if expected_prefix in f.name), None)

                if not match:
                    print(f"⚠️ No file found for: {scenario} / {label}")
                    continue

                ds = xr.open_dataset(match)
                if index_variable in ds:
                    index_da = ds[index_variable]
                elif len(ds.data_vars) == 1:
                    index_da = list(ds.data_vars.values())[0]
                else:
                    print(f"⚠️ Variable '{index_variable}' not found in {match.name}")
                    continue

                region_mask_da = region_mask.mask(index_da)
                regional_mean = index_da.groupby(region_mask_da).mean()

                df = pd.DataFrame({
                    "Veg_Biome": region_mask.names,
                    "Value": regional_mean.values
                })

                plot_data[(scenario, label)] = df
                all_values.extend(regional_mean.values)

        return plot_data, all_values

    # ───────── Collect data ─────────
    hist_labels = ["Baseline (1995–2014)"]
    future_labels = ["Near-term (2021–2040)", "Mid-term (2041–2060)", "Far-term (2081–2100)"]

    hist_plot_data, hist_values = collect_data(historical_scenarios, hist_labels)
    scen_plot_data, scen_values = collect_data(scenario_runs, future_labels)

    # Combine all values to get global min/max
    all_values = hist_values + scen_values
    if not all_values:
        raise ValueError("No valid data found for plotting.")

    global_vmin = np.nanmin(all_values)
    global_vmax = np.nanmax(all_values)

    def make_plot(plot_data, scenarios, labels, values, title, fname):
        n_rows = len(scenarios)
        n_cols = len(labels)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(6 * n_cols, 5 * n_rows), constrained_layout=True)
        axes = np.atleast_2d(axes)

        step = (global_vmax - global_vmin) / 5
        ticks = np.linspace(global_vmin, global_vmax, num=6)

        for i, scenario in enumerate(scenarios):
            for j, label in enumerate(labels):
                ax = axes[i, j]
                df = plot_data.get((scenario, label))
                if df is None:
                    ax.set_title(f"{scenario.upper()} — {label}\n(no data)", fontsize=10)
                    ax.set_axis_off()
                    continue

                merged = bioregions.merge(df, on="Veg_Biome")
                merged.plot(
                    column="Value",
                    cmap=cmap,
                    linewidth=0.5,
                    edgecolor="black",
                    legend=True,
                    legend_kwds={
                        "label": legend_label or f"{index_variable.replace('_', ' ').title()} (units)",
                        "orientation": "vertical",
                        "shrink": 0.7,
                        "ticks": ticks
                    },
                    ax=ax,
                    vmin=global_vmin,
                    vmax=global_vmax
                )

#               towns_gdf.plot(ax=ax, color='black', markersize=35, zorder=5)
#               for x, y, label_txt in zip(towns_gdf.geometry.x, towns_gdf.geometry.y, towns_gdf['city']):
#                   ax.text(x, y, label_txt, fontsize=8, ha='left', va='bottom')

                ax.set_title(f"{scenario.upper()} — {label}", fontsize=10)
                ax.set_axis_off()

        fig.suptitle(title, fontsize=16, y=1.02)

        if output_path:
            plot_file = Path(output_path) / fname
            plot_file.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(plot_file, dpi=300, bbox_inches='tight')
            print(f"✅ Plot saved to: {plot_file}")
        else:
            plt.show()

    # ───────── Make plots ─────────
    if hist_plot_data:
        make_plot(
            hist_plot_data,
            historical_scenarios,
            hist_labels,
            hist_values,
            "",
#           f"{index_variable.replace('_', ' ').title()} — Historical Baseline",
            f"{index_variable}_historical_baseline.png"
        )

    if scen_plot_data:
        make_plot(
            scen_plot_data,
            scenario_runs,
            future_labels,
            scen_values,
            "",
#           f"{index_variable.replace('_', ' ').title()} — Scenario Time Slices",
            f"{index_variable}_scenarios_timeslices.png"
        )