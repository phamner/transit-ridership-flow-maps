from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


GTFS_STOPS_PATH = Path("data/bart/gtfs/current/stops.txt")
EDGE_RIDERS_PATH = Path("output/bart/ridership/edge_riders_weekday_202606.csv")
OUTPUT_DIR = Path("output/bart")


def width_scale(values, min_width: float = 1.0, max_width: float = 11.0):
    min_value = values.min()
    max_value = values.max()

    if max_value == min_value:
        return [0.5 * (min_width + max_width)] * len(values)

    return [
        min_width + (max_width - min_width) * (value - min_value) / (max_value - min_value)
        for value in values
    ]


def stations_table() -> pd.DataFrame:
    stops = pd.read_csv(GTFS_STOPS_PATH)
    stops["station_id"] = stops["parent_station"].where(
        stops["parent_station"].notna() & (stops["parent_station"].astype(str) != ""),
        stops["stop_id"],
    )

    return (
        stops.groupby("station_id", as_index=False)
        .agg(
            station_name=("stop_name", "first"),
            stop_lat=("stop_lat", "mean"),
            stop_lon=("stop_lon", "mean"),
        )
        .reset_index(drop=True)
    )


def main() -> None:
    edges = pd.read_csv(EDGE_RIDERS_PATH).copy()
    stations = stations_table()
    station_lookup = stations.set_index("station_id")

    fig, ax = plt.subplots(figsize=(10, 14), dpi=180)
    ax.set_facecolor("#f7f7f5")
    fig.patch.set_facecolor("#f7f7f5")

    widths = width_scale(edges["riders_weekday"])

    for edge, width in zip(edges.itertuples(index=False), widths):
        a = station_lookup.loc[edge.station_a_id]
        b = station_lookup.loc[edge.station_b_id]

        ax.plot(
            [a["stop_lon"], b["stop_lon"]],
            [a["stop_lat"], b["stop_lat"]],
            color="#15395b",
            linewidth=width,
            alpha=1.0,
            solid_capstyle="round",
            zorder=2,
        )

    ax.scatter(
        stations["stop_lon"],
        stations["stop_lat"],
        s=20,
        color="#f1faee",
        edgecolor="#15395b",
        linewidth=0.8,
        zorder=3,
    )

    ax.set_title("BART Weekday Rider Flow\n(Edge width = assigned weekday riders)", fontsize=15, pad=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(True, color="#d9d9d6", linewidth=0.5, alpha=0.35)
    ax.set_aspect("equal", adjustable="datalim")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "flow_map_weekday_riders.png"
    plt.tight_layout()
    plt.savefig(output_path)

    print(f"Saved rider flow map to {output_path}")


if __name__ == "__main__":
    main()
