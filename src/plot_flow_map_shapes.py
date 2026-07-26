from pathlib import Path

import matplotlib.pyplot as plt

from gtfs.loader import GTFSFeed


GTFS_DIR = Path("data/bart/gtfs/current")
OUTPUT_DIR = Path("output/bart")
LINE_COLOR = "#1d3557"
LINE_ALPHA = 1.0


def width_scale(values, min_width: float = 0.8, max_width: float = 8.0):
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

    shapes = feed.shapes[["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]].copy()
    shape_counts = feed.shape_traversal_counts().copy()
    stations = feed.stations()[["station_id", "stop_lat", "stop_lon"]].copy()

    plot_shapes = shape_counts.merge(shapes, on="shape_id", how="inner")

    widths = width_scale(shape_counts["traversal_count"])
    width_lookup = dict(zip(shape_counts["shape_id"], widths))

    fig, ax = plt.subplots(figsize=(10, 14), dpi=180)
    ax.set_facecolor("#f7f7f5")
    fig.patch.set_facecolor("#f7f7f5")

    # Draw low-frequency shapes first so trunk shapes remain visually dominant.
    for shape in shape_counts.sort_values("traversal_count", ascending=True).itertuples(index=False):
        shape_points = plot_shapes[plot_shapes["shape_id"] == shape.shape_id].sort_values("shape_pt_sequence")
        ax.plot(
            shape_points["shape_pt_lon"],
            shape_points["shape_pt_lat"],
            color=LINE_COLOR,
            linewidth=width_lookup[shape.shape_id],
            alpha=LINE_ALPHA,
            solid_capstyle="round",
            zorder=2,
        )

    ax.scatter(
        stations["stop_lon"],
        stations["stop_lat"],
        s=9,
        color="#f1faee",
        edgecolor=LINE_COLOR,
        linewidth=0.5,
        zorder=3,
    )

    ax.set_title("BART Shape-Based Flow Prototype\n(Edge width = scheduled traversals by shape)", fontsize=15, pad=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    ax.grid(True, color="#d9d9d6", linewidth=0.5, alpha=0.35)
    ax.set_aspect("equal", adjustable="datalim")

    output_path = OUTPUT_DIR / "flow_map_shapes_traversal.png"
    plt.tight_layout()
    plt.savefig(output_path)

    print(f"Saved shape-based flow map to {output_path}")
    print(f"Rendered {shape_counts['shape_id'].nunique()} unique shapes")


if __name__ == "__main__":
    main()
