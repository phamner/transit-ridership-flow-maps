from __future__ import annotations

import re
import unicodedata
from collections import deque
from pathlib import Path

import pandas as pd


OD_LONG_PATH = Path("output/bart/ridership/od_long_202606.csv")
PHYSICAL_EDGES_PATH = Path("output/bart/physical_edges.csv")
GTFS_STOPS_PATH = Path("data/bart/gtfs/current/stops.txt")
OUTPUT_DIR = Path("output/bart/ridership")

TARGET_DAY_TYPE = "average_weekday"

# OD names occasionally use short labels while GTFS has the full station title.
OD_NAME_ALIASES = {
    "berkeley": "downtownberkeley",
    "civiccenter": "civiccenterunplaza",
    "millbrae": "millbraecaltraintransferplatform",
    "northconcord": "northconcordmartinez",
    "oaklandinternationalairport": "oaklandinternationalairportstation",
    "pleasanthill": "pleasanthillcontracostacentre",
    "warmsprings": "warmspringssouthfremont",
}


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", ascii_name)


def load_gtfs_stations() -> pd.DataFrame:
    stops = pd.read_csv(GTFS_STOPS_PATH)
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


def build_station_crosswalk(od_long: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
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

        if od_norm in station_index:
            station_id, station_name = station_index[od_norm]
            mappings.append(
                {
                    "od_station_code": row.od_station_code,
                    "od_station_name": row.od_station_name,
                    "gtfs_station_id": station_id,
                    "gtfs_station_name": station_name,
                    "match_method": "normalized_exact",
                }
            )
            continue

        alias_norm = OD_NAME_ALIASES.get(od_norm)
        if alias_norm and alias_norm in station_index:
            station_id, station_name = station_index[alias_norm]
            mappings.append(
                {
                    "od_station_code": row.od_station_code,
                    "od_station_name": row.od_station_name,
                    "gtfs_station_id": station_id,
                    "gtfs_station_name": station_name,
                    "match_method": "alias",
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
    for edge in physical_edges.itertuples(index=False):
        adjacency.setdefault(edge.station_a_id, set()).add(edge.station_b_id)
        adjacency.setdefault(edge.station_b_id, set()).add(edge.station_a_id)

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
        raise ValueError(f"Unroutable OD pairs found ({len(unroutable)}). Examples: {examples}")

    edge_flow = physical_edges.copy()
    edge_flow["riders_weekday"] = edge_flow.apply(
        lambda row: edge_rider_totals.get(tuple(sorted((row["station_a_id"], row["station_b_id"]))), 0.0),
        axis=1,
    )
    edge_flow = edge_flow.sort_values("riders_weekday", ascending=False).reset_index(drop=True)

    routes = pd.DataFrame(route_rows).sort_values("riders", ascending=False).reset_index(drop=True)
    return edge_flow, routes


def main() -> None:
    od_long = pd.read_csv(OD_LONG_PATH)
    physical_edges = pd.read_csv(PHYSICAL_EDGES_PATH)
    stations = load_gtfs_stations()

    crosswalk = build_station_crosswalk(od_long, stations)
    edge_flow, routed_pairs = assign_weekday_od_to_edges(od_long, crosswalk, physical_edges)

    period_candidates = sorted(set(od_long["period"].astype(str)))
    period = period_candidates[0] if period_candidates else "unknown"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    crosswalk_path = OUTPUT_DIR / f"station_code_crosswalk_{period}.csv"
    edge_flow_path = OUTPUT_DIR / f"edge_riders_weekday_{period}.csv"
    routed_pairs_path = OUTPUT_DIR / f"od_routed_weekday_{period}.csv"

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
