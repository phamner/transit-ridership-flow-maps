from pathlib import Path

from gtfs.loader import GTFSFeed


GTFS_DIR = Path("data/bart/gtfs/current")


def main() -> None:
	feed = GTFSFeed(GTFS_DIR)

	stops = feed.stops[["stop_id", "stop_name", "parent_station"]].head()
	print("Stops")
	print(stops.to_string(index=False))

	sample_trip = feed.trips.iloc[0]
	trip_id = sample_trip["trip_id"]

	print()
	print("Sample trip")
	print(
		sample_trip[["trip_id", "route_id", "trip_headsign", "direction_id", "shape_id"]]
		.to_string()
	)

	print()
	print("Trip stop sequence")
	trip_stop_times = feed.trip_stop_times(trip_id)
	print(trip_stop_times[["stop_sequence", "stop_id", "departure_time"]].head(12).to_string(index=False))

	print()
	print("Station-level summary")
	stations = feed.stations()[["station_id", "station_name", "platform_count"]].head()
	print(stations.to_string(index=False))


if __name__ == "__main__":
	main()