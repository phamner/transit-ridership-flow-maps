# Transit Ridership Flow Maps

Open-source transit analysis pipeline for producing Subway Builder-style ridership flow maps from public GTFS and OD data.

## Current focus

The first milestone is a clean GTFS inspection layer for BART that can later be generalized to other agencies.

This repository currently includes:

- a lightweight GTFS feed loader
- a script that inspects stops, trips, and station aggregation
- the BART GTFS feed under `data/bart/gtfs/current/`

## Run the inspection script

```bash
./.venv/bin/python src/read_gtfs.py
```

## Next pipeline steps

1. Parse trip-to-route relationships.
2. Confirm stop sequence semantics.
3. Derive station-level groupings from platform stops.
4. Build adjacent station pairs for graph construction.
