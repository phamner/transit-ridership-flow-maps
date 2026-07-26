from pathlib import Path

import matplotlib.pyplot as plt

from gtfs.loader import GTFSFeed


GTFS_DIR = Path("data/bart/gtfs/current")
OUTPUT_DIR = Path("output/bart")


def width_scale(values, min_width: float = 1.2, max_width: float = 10.0):
    min_value = values.min()
    max_value = values.max()

    if max_value == min_value:
        return [0.5 * (min_width + max_width)] * len(values)

    return [
        min_width + (max_width - min_width) * (value - min_value) / (max_value - min_value)
        for value in values
    ]


def main() -> None:
    feed = GTFSFeed(GTFS_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    stations = feed.stations()[["station_id", "station_name", "stop_lat", "stop_lon"]].copy()
    edges = feed.physical_edge_table().copy()

    station_lookup = stations.set_index("station_id")

    fig, ax = plt.subplots(figsize=(10, 14), dpi=180)
    ax.set_facecolor("#f7f7f5")
    fig.patch.set_facecolor("#f7f7f5")

    widths = width_scale(edges["traversal_count"])

    for edge, width in zip(edges.itertuples(index=False), widths):
        a = station_lookup.loc[edge.station_a_id]
        b = station_lookup.loc[edge.station_b_id]

        x_values = [a["stop_lon"], b["stop_lon"]]
        y_values = [a["stop_lat"], b["stop_lat"]]

        ax.plot(
            x_values,
            y_values,
            color="#1d3557",
            linewidth=width,
            alpha=0.86,
            solid_capstyle="round",
            zorder=2,
        )

    ax.scatter(
        stations["stop_lon"],
        stations["stop_lat"],
        s=20,
        color="#f1faee",
        edgecolor="#1d3557",
        linewidth=0.8,
        zorder=3,
    )

    ax.set_title("BART Network Flow Prototype\n(Edge width = scheduled traversals)", fontsize=15, pad=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    ax.grid(True, color="#d9d9d6", linewidth=0.5, alpha=0.35)
    ax.set_aspect("equal", adjustable="datalim")

    output_path = OUTPUT_DIR / "flow_map_traversal.png"
    plt.tight_layout()
    plt.savefig(output_path)

    print(f"Saved flow map to {output_path}")


if __name__ == "__main__":
    main()
