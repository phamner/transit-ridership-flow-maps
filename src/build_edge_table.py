from pathlib import Path

from gtfs.loader import GTFSFeed


GTFS_DIR = Path("data/bart/gtfs/current")
OUTPUT_DIR = Path("output/bart")


def main() -> None:
    feed = GTFSFeed(GTFS_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    station_pairs = feed.all_trip_station_pairs()
    physical_edges = feed.physical_edge_table()

    station_pairs_path = OUTPUT_DIR / "trip_station_pairs.csv"
    physical_edges_path = OUTPUT_DIR / "physical_edges.csv"

    station_pairs.to_csv(station_pairs_path, index=False)
    physical_edges.to_csv(physical_edges_path, index=False)

    print(f"Wrote {len(station_pairs):,} directional station pairs to {station_pairs_path}")
    print(f"Wrote {len(physical_edges):,} physical edges to {physical_edges_path}")

    print()
    print("Top 15 physical edges by traversal_count")
    preview = physical_edges.head(15)
    print(
        preview[
            [
                "station_a_name",
                "station_b_name",
                "traversal_count",
                "unique_trip_count",
                "route_count",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
