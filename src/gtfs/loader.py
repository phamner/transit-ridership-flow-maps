from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    def trip_stop_times(self, trip_id: str) -> pd.DataFrame:
        trip_stop_times = self.stop_times[self.stop_times["trip_id"] == trip_id].copy()
        return trip_stop_times.sort_values("stop_sequence")

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