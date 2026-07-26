from __future__ import annotations

import argparse
import re
import unicodedata
from collections import deque
from pathlib import Path

import pandas as pd

from agency_config import get_agency_config, latest_period_csv

TARGET_DAY_TYPE = "average_weekday"


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", ascii_name)


def load_gtfs_stations(gtfs_stops_path: Path) -> pd.DataFrame:
    stops = pd.read_csv(gtfs_stops_path)
    stops["station_id"] = stops["parent_station"].where(
        stops["parent_station"].notna() & (stops["parent_station"].astype(str) != ""),
        stops["stop_id"],
    )

    stations = (
        stops.groupby("station_id", as_index=False)
        .agg(
            station_name=("stop_name", "first"),
            stop_lat=("stop_lat", "mean"),
            stop_lon=("stop_lon", "mean"),
        )
        .reset_index(drop=True)
    )

    stations["station_name_norm"] = stations["station_name"].map(normalize_name)
    return stations


def build_station_crosswalk(od_long: pd.DataFrame, stations: pd.DataFrame, od_name_aliases: dict[str, str]) -> pd.DataFrame:
    od_lookup = (
        pd.concat(
            [
                od_long[["origin_code", "origin_station_name"]].rename(
                    columns={"origin_code": "od_station_code", "origin_station_name": "od_station_name"}
                ),
                od_long[["destination_code", "destination_station_name"]].rename(
                    columns={"destination_code": "od_station_code", "destination_station_name": "od_station_name"}
                ),
            ],
            ignore_index=True,
        )
        .dropna(subset=["od_station_code", "od_station_name"])
        .drop_duplicates()
        .sort_values("od_station_code")
        .reset_index(drop=True)
    )

    station_index = {
        row.station_name_norm: (row.station_id, row.station_name)
        for row in stations.itertuples(index=False)
    }

    mappings = []
    missing = []

    for row in od_lookup.itertuples(index=False):
        od_norm = normalize_name(row.od_station_name)

        match_method = None
        matched_name = None

        if od_norm in station_index:
            matched_name = od_norm
            match_method = "normalized_exact"
        else:
            alias_norm = od_name_aliases.get(od_norm)
            if alias_norm and alias_norm in station_index:
                matched_name = alias_norm
                match_method = "alias"
            else:
                for component in re.split(r"/", str(row.od_station_name)):
                    component_norm = normalize_name(component)
                    if component_norm in station_index:
                        matched_name = component_norm
                        match_method = "component"
                        break

        if matched_name:
            station_id, station_name = station_index[matched_name]
            mappings.append(
                {
                    "od_station_code": row.od_station_code,
                    "od_station_name": row.od_station_name,
                    "gtfs_station_id": station_id,
                    "gtfs_station_name": station_name,
                    "match_method": match_method,
                }
            )
            continue

        missing.append((row.od_station_code, row.od_station_name))

    if missing:
        missing_text = ", ".join([f"{code}:{name}" for code, name in missing])
        raise ValueError(f"Missing OD->GTFS station mappings for: {missing_text}")

    crosswalk = pd.DataFrame(mappings).drop_duplicates(subset=["od_station_code"]).reset_index(drop=True)
    return crosswalk


def shortest_path_nodes(adjacency: dict[str, set[str]], origin: str, destination: str) -> list[str] | None:
    if origin == destination:
        return [origin]

    visited = {origin}
    queue = deque([(origin, [origin])])

    while queue:
        node, path = queue.popleft()
        for neighbor in adjacency[node]:
            if neighbor in visited:
                continue
            next_path = path + [neighbor]
            if neighbor == destination:
                return next_path
            visited.add(neighbor)
            queue.append((neighbor, next_path))

    return None


def assign_weekday_od_to_edges(
    od_long: pd.DataFrame,
    crosswalk: pd.DataFrame,
    physical_edges: pd.DataFrame,
    gtfs_transfers: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    weekday_od = od_long[od_long["day_type"] == TARGET_DAY_TYPE].copy()
    weekday_od = weekday_od[~weekday_od["is_intrastation"]].copy()

    origin_xwalk = crosswalk[["od_station_code", "gtfs_station_id"]].rename(
        columns={"od_station_code": "origin_code", "gtfs_station_id": "origin_station_id"}
    )
    destination_xwalk = crosswalk[["od_station_code", "gtfs_station_id"]].rename(
        columns={"od_station_code": "destination_code", "gtfs_station_id": "destination_station_id"}
    )

    weekday_od = weekday_od.merge(origin_xwalk, on="origin_code", how="left")
    weekday_od = weekday_od.merge(destination_xwalk, on="destination_code", how="left")

    unmapped = weekday_od[
        weekday_od["origin_station_id"].isna() | weekday_od["destination_station_id"].isna()
    ]
    if not unmapped.empty:
        raise ValueError("Found unmapped weekday OD pairs after crosswalk join.")

    adjacency: dict[str, set[str]] = {}
    physical_edge_keys: set[tuple[str, str]] = set()
    for edge in physical_edges.itertuples(index=False):
        adjacency.setdefault(edge.station_a_id, set()).add(edge.station_b_id)
        adjacency.setdefault(edge.station_b_id, set()).add(edge.station_a_id)
        physical_edge_keys.add(tuple(sorted((str(edge.station_a_id), str(edge.station_b_id)))))

    if gtfs_transfers is not None and not gtfs_transfers.empty:
        transfer_pairs = gtfs_transfers[["from_station_id", "to_station_id"]].dropna().copy()
        transfer_pairs["from_station_id"] = transfer_pairs["from_station_id"].astype(str)
        transfer_pairs["to_station_id"] = transfer_pairs["to_station_id"].astype(str)

        for row in transfer_pairs.itertuples(index=False):
            adjacency.setdefault(row.from_station_id, set()).add(row.to_station_id)
            adjacency.setdefault(row.to_station_id, set()).add(row.from_station_id)

    edge_rider_totals: dict[tuple[str, str], float] = {}
    route_rows = []
    unroutable = []

    for row in weekday_od.itertuples(index=False):
        origin = str(row.origin_station_id)
        destination = str(row.destination_station_id)
        riders = float(row.riders)

        path_nodes = shortest_path_nodes(adjacency, origin, destination)
        if not path_nodes or len(path_nodes) < 2:
            unroutable.append((row.origin_code, row.destination_code))
            continue

        hops = list(zip(path_nodes[:-1], path_nodes[1:]))
        for a, b in hops:
            key = tuple(sorted((a, b)))
            if key in physical_edge_keys:
                edge_rider_totals[key] = edge_rider_totals.get(key, 0.0) + riders

        route_rows.append(
            {
                "origin_code": row.origin_code,
                "destination_code": row.destination_code,
                "origin_station_id": origin,
                "destination_station_id": destination,
                "riders": riders,
                "path_length_edges": len(hops),
                "path_station_ids": "|".join(path_nodes),
            }
        )

    if unroutable:
        examples = ", ".join([f"{o}->{d}" for o, d in unroutable[:10]])
        print(f"Warning: unroutable OD pairs found ({len(unroutable)}). Examples: {examples}")

    edge_flow = physical_edges.copy()
    edge_flow["riders_weekday"] = edge_flow.apply(
        lambda row: edge_rider_totals.get(tuple(sorted((row["station_a_id"], row["station_b_id"]))), 0.0),
        axis=1,
    )
    edge_flow = edge_flow.sort_values("riders_weekday", ascending=False).reset_index(drop=True)

    routes = pd.DataFrame(route_rows).sort_values("riders", ascending=False).reset_index(drop=True)
    return edge_flow, routes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign OD riders to physical station edges.")
    parser.add_argument("--agency", default="bart", help="Agency id from agency_config.py")
    parser.add_argument("--od-long", help="Optional path to od_long_YYYYMM.csv")
    parser.add_argument("--physical-edges", help="Optional path to physical_edges.csv")
    parser.add_argument("--output-dir", help="Optional output directory override")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = get_agency_config(args.agency)

    od_long_path: Path
    detected_period: str
    if args.od_long:
        od_long_path = Path(args.od_long)
        period_matches = re.search(r"(\d{6})", od_long_path.name)
        detected_period = period_matches.group(1) if period_matches else "unknown"
    else:
        od_long_path, detected_period = latest_period_csv(cfg.ridership_output_dir, "od_long")

    physical_edges_path = Path(args.physical_edges) if args.physical_edges else (cfg.output_dir / "physical_edges.csv")
    output_dir = Path(args.output_dir) if args.output_dir else cfg.ridership_output_dir
    gtfs_stops_path = cfg.gtfs_dir / "stops.txt"

    od_long = pd.read_csv(od_long_path)
    physical_edges = pd.read_csv(physical_edges_path)
    transfers_path = cfg.gtfs_dir / "transfers.txt"
    gtfs_transfers = None
    if transfers_path.exists():
        transfers_raw = pd.read_csv(transfers_path)
        stop_map = pd.read_csv(gtfs_stops_path)[["stop_id", "parent_station"]].copy()
        stop_map["station_id"] = stop_map["parent_station"].where(
            stop_map["parent_station"].notna() & (stop_map["parent_station"].astype(str) != ""),
            stop_map["stop_id"],
        )
        stop_map = stop_map[["stop_id", "station_id"]].drop_duplicates()
        gtfs_transfers = (
            transfers_raw.merge(stop_map, left_on="from_stop_id", right_on="stop_id", how="left")
            .rename(columns={"station_id": "from_station_id"})
            .drop(columns=["stop_id"])
            .merge(stop_map, left_on="to_stop_id", right_on="stop_id", how="left")
            .rename(columns={"station_id": "to_station_id"})
            .drop(columns=["stop_id"])
        )
    stations = load_gtfs_stations(gtfs_stops_path)

    crosswalk = build_station_crosswalk(od_long, stations, cfg.od_name_aliases)
    edge_flow, routed_pairs = assign_weekday_od_to_edges(
        od_long,
        crosswalk,
        physical_edges,
        gtfs_transfers=gtfs_transfers,
    )

    period_candidates = sorted(set(od_long["period"].astype(str)))
    period = period_candidates[0] if period_candidates else detected_period

    output_dir.mkdir(parents=True, exist_ok=True)

    crosswalk_path = output_dir / f"station_code_crosswalk_{period}.csv"
    edge_flow_path = output_dir / f"edge_riders_weekday_{period}.csv"
    routed_pairs_path = output_dir / f"od_routed_weekday_{period}.csv"

    crosswalk.to_csv(crosswalk_path, index=False)
    edge_flow.to_csv(edge_flow_path, index=False)
    routed_pairs.to_csv(routed_pairs_path, index=False)

    total_weekday_riders = (
        od_long[(od_long["day_type"] == TARGET_DAY_TYPE) & (~od_long["is_intrastation"])]["riders"].sum()
    )
    routed_riders = routed_pairs["riders"].sum()

    print(f"Wrote crosswalk: {crosswalk_path}")
    print(f"Wrote weekday edge rider totals: {edge_flow_path}")
    print(f"Wrote routed weekday OD pairs: {routed_pairs_path}")
    print()
    print(f"Crosswalk stations: {len(crosswalk)}")
    print(f"Weekday non-intrastation OD pairs routed: {len(routed_pairs)}")
    print(f"Total weekday riders in OD slice: {total_weekday_riders:,.2f}")
    print(f"Total weekday riders routed: {routed_riders:,.2f}")

    rider_delta = abs(total_weekday_riders - routed_riders)
    print(f"Rider conservation delta: {rider_delta:,.6f}")

    print()
    print("Top 15 edges by weekday riders")
    print(
        edge_flow[
            ["station_a_name", "station_b_name", "riders_weekday", "traversal_count", "route_count"]
        ]
        .head(15)
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
