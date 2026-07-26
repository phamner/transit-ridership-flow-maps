from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import pandas as pd

from agency_config import get_agency_config, latest_period_csv
from gtfs.loader import GTFSFeed

LINE_ALPHA = 1.0
RIDERS_SCALE_MIN = 0.0
RIDERS_SCALE_MAX = 150000.0
MIN_LINE_WIDTH = 0.9
MAX_LINE_WIDTH = 12.0


def width_scale(values, min_width: float = MIN_LINE_WIDTH, max_width: float = MAX_LINE_WIDTH):
    min_value = values.min()
    max_value = values.max()

    if max_value == min_value:
        return [0.5 * (min_width + max_width)] * len(values)

    return [
        min_width + (max_width - min_width) * (value - min_value) / (max_value - min_value)
        for value in values
    ]


def width_for_value(
    value: float,
    min_value: float,
    max_value: float,
    min_width: float = MIN_LINE_WIDTH,
    max_width: float = MAX_LINE_WIDTH,
) -> float:
    if max_value == min_value:
        return 0.5 * (min_width + max_width)

    clamped = min(max(float(value), float(min_value)), float(max_value))
    return min_width + (max_width - min_width) * (clamped - min_value) / (max_value - min_value)


def edge_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((str(a), str(b))))


def collapse_consecutive_stations(trip_stops: pd.DataFrame) -> pd.DataFrame:
    collapsed = trip_stops.sort_values("stop_sequence").copy()
    collapsed = collapsed[collapsed["station_id"].notna()].copy()
    collapsed["station_id"] = collapsed["station_id"].astype(str)
    return collapsed[collapsed["station_id"].ne(collapsed["station_id"].shift())].reset_index(drop=True)


def nearest_point_index(shape_points: pd.DataFrame, station_lon: float, station_lat: float) -> int:
    lon_delta = shape_points["shape_pt_lon"].to_numpy() - float(station_lon)
    lat_delta = shape_points["shape_pt_lat"].to_numpy() - float(station_lat)
    distances = lon_delta * lon_delta + lat_delta * lat_delta
    return int(distances.argmin())


def representative_shape_segments(feed: GTFSFeed, edge_riders: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    stops = feed.stops_with_station_ids()[["stop_id", "station_id", "station_name"]].copy()
    trips = feed.trips[["trip_id", "shape_id"]].dropna(subset=["shape_id"]).copy()
    trips = trips.sort_values(["shape_id", "trip_id"]).drop_duplicates(subset=["shape_id"], keep="first")
    stations = stations.set_index("station_id")
    shape_lookup = {
        shape_id: frame.sort_values("shape_pt_sequence").reset_index(drop=True).copy()
        for shape_id, frame in feed.shapes[["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]].groupby("shape_id")
    }

    rider_lookup = {
        edge_key(row.station_a_id, row.station_b_id): float(row.riders_weekday)
        for row in edge_riders.itertuples(index=False)
    }

    segment_rows = []

    for trip in trips.itertuples(index=False):
        trip_stop_times = feed.trip_stop_times(trip.trip_id).merge(stops, on="stop_id", how="left")
        trip_stop_times = trip_stop_times[["trip_id", "stop_sequence", "stop_id", "station_id", "station_name"]].copy()
        trip_stop_times = collapse_consecutive_stations(trip_stop_times)

        if len(trip_stop_times) < 2:
            continue

        shape_points = shape_lookup.get(trip.shape_id)
        if shape_points is None or shape_points.empty:
            continue

        trip_stop_times["station_lon"] = trip_stop_times["station_id"].map(stations["stop_lon"])
        trip_stop_times["station_lat"] = trip_stop_times["station_id"].map(stations["stop_lat"])
        trip_stop_times["shape_point_index"] = trip_stop_times.apply(
            lambda row: nearest_point_index(shape_points, row["station_lon"], row["station_lat"]),
            axis=1,
        )

        for current_row, next_row in zip(trip_stop_times.itertuples(index=False), trip_stop_times.iloc[1:].itertuples(index=False)):
            riders = rider_lookup.get(edge_key(current_row.station_id, next_row.station_id), 0.0)
            segment_rows.append(
                {
                    "shape_id": trip.shape_id,
                    "trip_id": trip.trip_id,
                    "from_station_id": current_row.station_id,
                    "from_station_name": current_row.station_name,
                    "to_station_id": next_row.station_id,
                    "to_station_name": next_row.station_name,
                    "start_idx": int(current_row.shape_point_index),
                    "end_idx": int(next_row.shape_point_index),
                    "riders_weekday": riders,
                }
            )

    segments = pd.DataFrame(segment_rows)
    if segments.empty:
        raise RuntimeError("No shape segments could be built from representative trips.")

    # Keep one row per shape segment; the representative trip provides the geometry,
    # and the physical edge table provides the rider total for that station pair.
    segments = (
        segments.groupby(
            [
                "shape_id",
                "from_station_id",
                "to_station_id",
                "from_station_name",
                "to_station_name",
                "start_idx",
                "end_idx",
            ],
            as_index=False,
        )
        .agg(riders_weekday=("riders_weekday", "max"), trip_id=("trip_id", "first"))
        .sort_values(["shape_id", "start_idx"])
        .reset_index(drop=True)
    )

    return segments


def load_weekday_station_totals(od_long: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    weekday = od_long[od_long["day_type"] == "average_weekday"].copy()

    origin_xwalk = crosswalk[["od_station_code", "gtfs_station_id"]].rename(
        columns={"od_station_code": "origin_code", "gtfs_station_id": "origin_station_id"}
    )
    destination_xwalk = crosswalk[["od_station_code", "gtfs_station_id"]].rename(
        columns={"od_station_code": "destination_code", "gtfs_station_id": "destination_station_id"}
    )

    weekday = weekday.merge(origin_xwalk, on="origin_code", how="left")
    weekday = weekday.merge(destination_xwalk, on="destination_code", how="left")

    boardings = weekday.groupby("origin_station_id", as_index=False).agg(boardings=("riders", "sum"))
    boardings = boardings.rename(columns={"origin_station_id": "station_id"})

    alightings = weekday.groupby("destination_station_id", as_index=False).agg(alightings=("riders", "sum"))
    alightings = alightings.rename(columns={"destination_station_id": "station_id"})

    totals = boardings.merge(alightings, on="station_id", how="outer").fillna(0.0)
    totals["weekday_station_ridership"] = totals["boardings"] + totals["alightings"]
    return totals[["station_id", "boardings", "alightings", "weekday_station_ridership"]]


def slice_shape_geometry(shape_points: pd.DataFrame, start_idx: int, end_idx: int) -> pd.DataFrame:
    points = shape_points.sort_values("shape_pt_sequence").reset_index(drop=True).copy()
    lower = min(int(start_idx), int(end_idx))
    upper = max(int(start_idx), int(end_idx))

    inside = points.iloc[lower : upper + 1].copy()
    if len(inside) >= 2:
        return inside

    selected = points.iloc[sorted(set([max(0, lower), min(len(points) - 1, upper)]))].copy()
    if len(selected) < 2 and len(points) >= 2:
        selected = points.iloc[[max(0, lower - 1), min(len(points) - 1, upper + 1)]].copy()

    return selected


def render_station_table_figure(
    station_table: pd.DataFrame,
    output_dir: Path,
    period_code: str,
    period_label: str,
    agency_display_name: str,
) -> Path:
    table_fig = plt.figure(figsize=(11, 18), dpi=300)
    table_ax = table_fig.add_axes([0.03, 0.03, 0.94, 0.9])
    table_ax.set_facecolor("#f7f7f5")
    table_fig.patch.set_facecolor("#f7f7f5")
    table_ax.axis("off")

    table_ax.set_title(
        f"{agency_display_name} station activity\n"
        f"Average weekday, {period_label}",
        fontsize=15,
        pad=18,
    )
    table_ax.text(
        0.0,
        1.0,
        "Ranked from most to least station activity (boardings + alightings).",
        transform=table_ax.transAxes,
        fontsize=9.5,
        color="#3b4a59",
        va="bottom",
    )

    table = table_ax.table(
        cellText=station_table[["rank", "station_name", "weekday_station_ridership_formatted"]].values.tolist(),
        colLabels=["#", "Station", "Avg weekday activity (in + out)"],
        cellLoc="left",
        colLoc="left",
        colWidths=[0.10, 0.68, 0.22],
        loc="upper left",
        bbox=[0.0, 0.0, 1.0, 0.96],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.2)
    table.scale(1.0, 1.05)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d9d9d6")
        cell.set_linewidth(0.3)
        if row == 0:
            cell.set_facecolor("#dfe8ef")
            cell.set_text_props(weight="bold", color="#10263b")
        elif row % 2 == 0:
            cell.set_facecolor("#f3f6f8")
        else:
            cell.set_facecolor("#fbfcfd")

    for spine in table_ax.spines.values():
        spine.set_edgecolor("#d9d9d6")
        spine.set_linewidth(0.8)

    output_path = output_dir / f"station_ridership_weekday_table_{period_code}.png"
    table_fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(table_fig)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render shape-based weekday rider flow map.")
    parser.add_argument("--agency", default="bart", help="Agency id from agency_config.py")
    parser.add_argument("--period", help="Period code to render, e.g. 202606")
    parser.add_argument("--edge-riders", help="Optional path to edge_riders_weekday_YYYYMM.csv")
    parser.add_argument("--od-long", help="Optional path to od_long_YYYYMM.csv")
    parser.add_argument("--crosswalk", help="Optional path to station_code_crosswalk_YYYYMM.csv")
    return parser.parse_args()


def _period_label(period_code: str) -> str:
    match = re.fullmatch(r"(\d{4})(\d{2})", period_code)
    if not match:
        return period_code
    year = int(match.group(1))
    month = int(match.group(2))
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    if 1 <= month <= 12:
        return f"{month_names[month - 1]} {year}"
    return period_code


def main() -> None:
    args = parse_args()
    cfg = get_agency_config(args.agency)

    if args.period:
        period_code = args.period
        edge_riders_path = Path(args.edge_riders) if args.edge_riders else cfg.ridership_output_dir / f"edge_riders_weekday_{period_code}.csv"
        od_long_path = Path(args.od_long) if args.od_long else cfg.ridership_output_dir / f"od_long_{period_code}.csv"
        crosswalk_path = Path(args.crosswalk) if args.crosswalk else cfg.ridership_output_dir / f"station_code_crosswalk_{period_code}.csv"
    else:
        edge_riders_path, period_code = latest_period_csv(cfg.ridership_output_dir, "edge_riders_weekday")
        od_long_path = Path(args.od_long) if args.od_long else cfg.ridership_output_dir / f"od_long_{period_code}.csv"
        crosswalk_path = Path(args.crosswalk) if args.crosswalk else cfg.ridership_output_dir / f"station_code_crosswalk_{period_code}.csv"

    line_color = cfg.line_color
    period_label = _period_label(period_code)

    feed = GTFSFeed(cfg.gtfs_dir)
    edge_riders = pd.read_csv(edge_riders_path)
    od_long = pd.read_csv(od_long_path)
    crosswalk = pd.read_csv(crosswalk_path)
    stations = feed.stations()[["station_id", "station_name", "stop_lat", "stop_lon"]].copy()
    shapes = feed.shapes[["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]].copy()
    station_totals = load_weekday_station_totals(od_long, crosswalk)
    stations = stations.merge(station_totals, on="station_id", how="left").fillna(0.0)

    segments = representative_shape_segments(feed, edge_riders, stations)
    segment_output_path = cfg.ridership_output_dir / f"shape_segment_riders_weekday_{period_code}.csv"
    station_totals_output_path = cfg.ridership_output_dir / f"station_ridership_weekday_{period_code}.csv"
    cfg.ridership_output_dir.mkdir(parents=True, exist_ok=True)
    segments.to_csv(segment_output_path, index=False)
    stations[["station_id", "station_name", "boardings", "alightings", "weekday_station_ridership"]].to_csv(
        station_totals_output_path,
        index=False,
    )

    ordered_segments = segments.sort_values("riders_weekday", ascending=True).reset_index(drop=True)
    widths = [
        width_for_value(value, RIDERS_SCALE_MIN, RIDERS_SCALE_MAX)
        for value in ordered_segments["riders_weekday"].to_list()
    ]

    station_table = (
        stations[["station_name", "weekday_station_ridership"]]
        .sort_values("weekday_station_ridership", ascending=False)
        .reset_index(drop=True)
    )
    station_table.insert(0, "rank", station_table.index + 1)
    station_table["weekday_station_ridership_formatted"] = station_table["weekday_station_ridership"].map(lambda value: f"{value:,.0f}")

    table_output_path = render_station_table_figure(
        station_table,
        output_dir=cfg.output_dir,
        period_code=period_code,
        period_label=period_label,
        agency_display_name=cfg.display_name,
    )

    fig, ax = plt.subplots(figsize=(13.5, 13.5), dpi=300)

    ax.set_facecolor("#f7f7f5")
    fig.patch.set_facecolor("#f7f7f5")

    shape_lookup = {shape_id: frame.copy() for shape_id, frame in shapes.groupby("shape_id")}

    for segment, width in zip(ordered_segments.itertuples(index=False), widths):
        shape_points = shape_lookup.get(segment.shape_id)
        if shape_points is None or shape_points.empty:
            continue

        segment_points = slice_shape_geometry(shape_points, segment.start_idx, segment.end_idx)
        if len(segment_points) < 2:
            continue

        ax.plot(
            segment_points["shape_pt_lon"],
            segment_points["shape_pt_lat"],
            color=line_color,
            linewidth=width,
            alpha=LINE_ALPHA,
            solid_capstyle="round",
            zorder=2,
        )

    ax.scatter(
        stations["stop_lon"],
        stations["stop_lat"],
        s=20,
        color="#f1faee",
        edgecolor=line_color,
        linewidth=0.8,
        zorder=3,
    )

    ax.set_title(
        f"{cfg.display_name} Weekday Rider Flow on GTFS Shapes\n"
        f"(Edge width = modeled weekday OD riders per segment, {period_label})",
        fontsize=15,
        pad=14,
    )

    lon_min = float(shapes["shape_pt_lon"].min())
    lon_max = float(shapes["shape_pt_lon"].max())
    lat_min = float(shapes["shape_pt_lat"].min())
    lat_max = float(shapes["shape_pt_lat"].max())
    lon_pad = (lon_max - lon_min) * 0.007
    lat_pad = (lat_max - lat_min) * 0.007

    ax.set_xlim(lon_min - lon_pad, lon_max + lon_pad)
    ax.set_ylim(lat_min - lat_pad, lat_max + lat_pad)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    min_riders = RIDERS_SCALE_MIN
    max_riders = RIDERS_SCALE_MAX

    legend_ax = ax.inset_axes([0.03, 0.03, 0.38, 0.24])
    legend_ax.set_facecolor((0.97, 0.97, 0.96, 0.96))
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.set_xticks([])
    legend_ax.set_yticks([])

    for spine in legend_ax.spines.values():
        spine.set_edgecolor("#d9d9d6")
        spine.set_linewidth(0.8)

    legend_ax.text(0.05, 0.9, "Ridership scale", fontsize=11.5, fontweight="bold", color="#10263b")
    legend_ax.text(0.05, 0.79, "Modeled average weekday riders per segment", fontsize=9.4, color="#3b4a59")

    x_start = 0.07
    x_end = 0.80
    y_center = 0.43

    # Draw a single scale line that smoothly increases in thickness.
    n_segments = 80
    for i in range(n_segments):
        fraction_left = i / n_segments
        fraction_right = (i + 1) / n_segments
        x_left = x_start + (x_end - x_start) * fraction_left
        x_right = x_start + (x_end - x_start) * fraction_right
        riders_mid = min_riders + (max_riders - min_riders) * (fraction_left + fraction_right) * 0.5
        linewidth = width_for_value(riders_mid, min_riders, max_riders)
        legend_ax.plot([x_left, x_right], [y_center, y_center], color=line_color, linewidth=linewidth, solid_capstyle="round")

    label_fractions = [0.0, 0.25, 0.5, 0.75, 1.0]
    for fraction in label_fractions:
        x_label = x_start + (x_end - x_start) * fraction
        riders_value = min_riders + (max_riders - min_riders) * fraction
        legend_ax.plot([x_label, x_label], [0.58, 0.66], color="#526273", linewidth=0.9)
        legend_ax.text(
            x_label,
            0.69,
            f"{int(round(riders_value / 1000.0)):d}k",
            ha="center",
            va="bottom",
            fontsize=8.4,
            color="#10263b",
        )

    legend_ax.text(0.05, 0.11, "From 0 to 150k riders", fontsize=8.6, color="#526273")
    legend_ax.text(
        0.05,
        0.04,
        "Station table uses activity = boardings + alightings (double-counts trips by design).",
        fontsize=7.2,
        color="#526273",
    )

    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.94)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = cfg.output_dir / "flow_map_weekday_riders_shapes.png"
    plt.savefig(output_path, dpi=300)
    plt.close(fig)

    print(f"Saved rider shape flow map to {output_path}")
    print(f"Saved station table to {table_output_path}")
    print(f"Saved segment table to {segment_output_path}")
    print(f"Saved station ridership table to {station_totals_output_path}")
    print(f"Rendered {segments['shape_id'].nunique()} unique shapes and {len(segments)} segments")


if __name__ == "__main__":
    main()