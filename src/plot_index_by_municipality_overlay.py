
def normalize_label(label):
    return (
        label.lower()
        .replace("–", "-")  # en dash
        .replace("—", "-")  # em dash
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )



def plot_time_slices_by_municipality_overlay(
    index_name,
    data_dir,
    shapefile_path,
    towns_csv_path,
    index_variable,
    time_labels,
    scenario_order,
    cmap="YlOrRd",
    output_path=None,
    legend_label=None,
    vmin=None,
    vmax=None
):
    from pathlib import Path
    import xarray as xr
    import geopandas as gpd
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt

    data_dir = Path(data_dir)
    all_files = list(data_dir.glob("*.nc"))

    municipalities = gpd.read_file(shapefile_path).to_crs("EPSG:4326")
    towns_df = pd.read_csv(towns_csv_path, sep=';')
    towns_df.columns = towns_df.columns.str.strip()
    towns_gdf = gpd.GeoDataFrame(
        towns_df,
        geometry=gpd.points_from_xy(towns_df['lng'], towns_df['lat']),
        crs="EPSG:4326"
    )

    historical_scenarios = [s for s in scenario_order if s == "historical"]
    scenario_runs = [s for s in scenario_order if s != "historical"]

    def normalize_label(label):
        return (
            label.lower()
            .replace("\u2013", "-")
            .replace("\u2014", "-")
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
        )

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
                    print(f"\u26a0\ufe0f No file found for: {scenario} / {label}")
                    continue

                ds = xr.open_dataset(match)
                if index_variable in ds:
                    index_da = ds[index_variable]
                elif len(ds.data_vars) == 1:
                    index_da = list(ds.data_vars.values())[0]
                else:
                    print(f"\u26a0\ufe0f Variable '{index_variable}' not found in {match.name}")
                    continue

                plot_data[(scenario, label)] = index_da
                all_values.extend(index_da.values.flatten())

        return plot_data, all_values

    hist_labels = ["Baseline (1995–2014)"]
    future_labels = ["Near-term (2021–2040)", "Mid-term (2041–2060)", "Far-term (2081–2100)"]

    hist_plot_data, hist_values = collect_data(historical_scenarios, hist_labels)
    scen_plot_data, scen_values = collect_data(scenario_runs, future_labels)

    all_values = np.array(hist_values + scen_values)
    if not all_values.size:
        raise ValueError("No valid data found for plotting.")

    global_vmin = np.nanmin(all_values) if vmin is None else vmin
    global_vmax = np.nanmax(all_values) if vmax is None else vmax
    ticks = np.round(np.linspace(global_vmin, global_vmax, num=6)).astype(int)

    def make_plot(plot_data, scenarios, labels, title, fname):
        n_rows = len(scenarios)
        n_cols = len(labels)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(6 * n_cols, 5 * n_rows), constrained_layout=True)
        axes = np.atleast_2d(axes)

        for i, scenario in enumerate(scenarios):
            for j, label in enumerate(labels):
                ax = axes[i, j]
                index_da = plot_data.get((scenario, label))
                if index_da is None:
                    ax.set_title(f"{scenario.upper()} — {label}\n(no data)", fontsize=10)
                    ax.set_axis_off()
                    continue

                index_da.plot(
                    ax=ax,
                    cmap=cmap,
                    vmin=global_vmin,
                    vmax=global_vmax,
                    cbar_kwargs={
                        "label": legend_label or f"{index_variable.replace('_', ' ').title()} (units)",
                        "orientation": "vertical",
                        "shrink": 0.7,
                        "ticks": ticks
                    }
                )

                municipalities.boundary.plot(ax=ax, edgecolor="black", linewidth=0.5)
                # towns_gdf.plot(ax=ax, color='black', markersize=15, zorder=5)
                ax.set_title(f"{scenario.upper()} — {label}", fontsize=10)
                ax.set_axis_off()

        fig.suptitle(title, fontsize=16, y=1.02)

        if output_path:
            plot_file = Path(output_path) / fname
            plot_file.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(plot_file, dpi=300, bbox_inches='tight')
            print(f"✅ Overlay plot saved to: {plot_file}")
        else:
            plt.show()

    if hist_plot_data:
        make_plot(
            hist_plot_data,
            historical_scenarios,
            hist_labels,
            "",
            f"{index_variable}_historical_overlay_municipalities.png"
        )

    if scen_plot_data:
        make_plot(
            scen_plot_data,
            scenario_runs,
            future_labels,
            "",
            f"{index_variable}_scenarios_overlay_municipalities.png"
        )