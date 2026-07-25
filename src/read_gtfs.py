from pathlib import Path
import pandas as pd

GTFS_DIR = Path("data/bart/gtfs/current")

stops = pd.read_csv(GTFS_DIR / "stops.txt")

print(stops[["stop_id", "stop_name"]].head())