from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


class GTFSFeed:
    """Lightweight GTFS table loader with a few reusable inspection helpers."""

    def __init__(self, feed_dir: Path) -> None:
        self.feed_dir = Path(feed_dir)

    @lru_cache(maxsize=None)
    def table(self, name: str) -> pd.DataFrame:
        return pd.read_csv(self.feed_dir / f"{name}.txt")

    @property
    def stops(self) -> pd.DataFrame:
        return self.table("stops")

    @property
    def trips(self) -> pd.DataFrame:
        return self.table("trips")

    @property
    def stop_times(self) -> pd.DataFrame:
        return self.table("stop_times")

    @property
    def shapes(self) -> pd.DataFrame:
        return self.table("shapes")

    def shape_traversal_counts(self) -> pd.DataFrame:
        trips = self.trips[["trip_id", "shape_id", "route_id", "service_id"]].copy()
        trips = trips[trips["shape_id"].notna()].copy()

        return (
            trips.groupby("shape_id", as_index=False)
            .agg(
                traversal_count=("trip_id", "size"),
                unique_trip_count=("trip_id", "nunique"),
                route_count=("route_id", "nunique"),
                service_count=("service_id", "nunique"),
            )
            .sort_values("traversal_count", ascending=False)
            .reset_index(drop=True)
        )

    def stops_with_station_ids(self) -> pd.DataFrame:
        stops = self.stops[["stop_id", "stop_name", "parent_station"]].copy()
        stops["station_id"] = stops["parent_station"].where(
            stops["parent_station"].notna() & (stops["parent_station"].astype(str) != ""),
            stops["stop_id"],
        )

        station_names = self.stations()[["station_id", "station_name"]]
        return stops.merge(station_names, on="station_id", how="left")

    def trip_stop_times(self, trip_id: str) -> pd.DataFrame:
        trip_stop_times = self.stop_times[self.stop_times["trip_id"] == trip_id].copy()
        return trip_stop_times.sort_values("stop_sequence")

    def trip_station_sequence(self, trip_id: str) -> pd.DataFrame:
        trip_stop_times = self.trip_stop_times(trip_id)
        stops = self.stops_with_station_ids()

        station_stop_times = trip_stop_times.merge(
            stops,
            on="stop_id",
            how="left",
        )

        station_stop_times = station_stop_times.sort_values("stop_sequence")
        station_stop_times = station_stop_times[station_stop_times["station_id"].notna()].copy()
        station_stop_times["station_id"] = station_stop_times["station_id"].astype(str)
        station_stop_times = station_stop_times[
            station_stop_times["station_id"].ne(station_stop_times["station_id"].shift())
        ]

        return station_stop_times[
            ["stop_sequence", "stop_id", "station_id", "station_name", "departure_time"]
        ].reset_index(drop=True)

    def trip_station_pairs(self, trip_id: str) -> pd.DataFrame:
        station_sequence = self.trip_station_sequence(trip_id)
        pairs = station_sequence[["stop_sequence", "station_id", "station_name"]].copy()
        pairs["next_station_id"] = pairs["station_id"].shift(-1)
        pairs["next_station_name"] = pairs["station_name"].shift(-1)
        pairs = pairs[pairs["next_station_id"].notna()].copy()

        return pairs[
            [
                "stop_sequence",
                "station_id",
                "station_name",
                "next_station_id",
                "next_station_name",
            ]
        ].reset_index(drop=True)

    def all_trip_station_pairs(self) -> pd.DataFrame:
        stop_times = self.stop_times[["trip_id", "stop_sequence", "stop_id"]].copy()
        stops = self.stops_with_station_ids()[["stop_id", "station_id", "station_name"]].copy()
        trips = self.trips[["trip_id", "route_id", "direction_id", "service_id"]].copy()

        station_stop_times = stop_times.merge(stops, on="stop_id", how="left")
        station_stop_times = station_stop_times[station_stop_times["station_id"].notna()].copy()
        station_stop_times["station_id"] = station_stop_times["station_id"].astype(str)
        station_stop_times = station_stop_times.sort_values(["trip_id", "stop_sequence"])

        previous_station = station_stop_times.groupby("trip_id")["station_id"].shift()
        station_stop_times = station_stop_times[station_stop_times["station_id"].ne(previous_station)].copy()

        station_stop_times["to_station_id"] = station_stop_times.groupby("trip_id")["station_id"].shift(-1)
        station_stop_times["to_station_name"] = station_stop_times.groupby("trip_id")["station_name"].shift(-1)

        pairs = station_stop_times[station_stop_times["to_station_id"].notna()].copy()
        pairs = pairs.rename(
            columns={
                "station_id": "from_station_id",
                "station_name": "from_station_name",
            }
        )

        pairs = pairs.merge(trips, on="trip_id", how="left")

        return pairs[
            [
                "trip_id",
                "route_id",
                "direction_id",
                "service_id",
                "stop_sequence",
                "from_station_id",
                "from_station_name",
                "to_station_id",
                "to_station_name",
            ]
        ].reset_index(drop=True)

    def physical_edge_table(self) -> pd.DataFrame:
        pairs = self.all_trip_station_pairs().copy()

        from_id = pairs["from_station_id"].astype(str)
        to_id = pairs["to_station_id"].astype(str)
        from_name = pairs["from_station_name"].astype(str)
        to_name = pairs["to_station_name"].astype(str)

        pairs["station_a_id"] = np.where(from_id <= to_id, from_id, to_id)
        pairs["station_b_id"] = np.where(from_id <= to_id, to_id, from_id)
        pairs["station_a_name"] = np.where(from_id <= to_id, from_name, to_name)
        pairs["station_b_name"] = np.where(from_id <= to_id, to_name, from_name)

        edge_table = (
            pairs.groupby(
                ["station_a_id", "station_b_id", "station_a_name", "station_b_name"],
                as_index=False,
            )
            .agg(
                traversal_count=("trip_id", "size"),
                unique_trip_count=("trip_id", "nunique"),
                route_count=("route_id", "nunique"),
            )
            .sort_values("traversal_count", ascending=False)
            .reset_index(drop=True)
        )

        return edge_table

    def trip_route(self, trip_id: str) -> pd.Series:
        matches = self.trips[self.trips["trip_id"] == trip_id]
        if matches.empty:
            raise KeyError(f"Unknown trip_id: {trip_id}")

        return matches.iloc[0]

    def stations(self) -> pd.DataFrame:
        stops = self.stops.copy()
        stops["station_id"] = stops["parent_station"].where(
            stops["parent_station"].notna() & (stops["parent_station"].astype(str) != ""),
            stops["stop_id"],
        )

        grouped = stops.groupby("station_id", as_index=False).agg(
            station_name=("stop_name", "first"),
            platform_count=("stop_id", "count"),
            platform_ids=("stop_id", lambda values: ", ".join(values)),
            stop_lat=("stop_lat", "mean"),
            stop_lon=("stop_lon", "mean"),
        )

        return grouped.sort_values(["station_name", "station_id"]).reset_index(drop=True)