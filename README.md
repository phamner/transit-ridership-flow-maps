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

## Build station edge tables

```bash
./.venv/bin/python src/build_edge_table.py
```

This writes:

- `output/bart/trip_station_pairs.csv`: directional station-to-station traversals by trip
- `output/bart/physical_edges.csv`: de-duplicated physical station pairs with traversal counts

## Render a first flow map

```bash
./.venv/bin/python src/plot_flow_map.py
```

This writes `output/bart/flow_map_traversal.png` using one neutral color and edge thickness based on scheduled traversals.

## Render a shape-based flow map

```bash
./.venv/bin/python src/plot_flow_map_shapes.py
```

This writes `output/bart/flow_map_shapes_traversal.png` using GTFS `shapes.txt` curves and thickness based on trips per shape.

## Download latest BART ridership OD data

```bash
./.venv/bin/python src/download_bart_ridership.py
```

This downloads:

- `data/bart/ridership/monthly/Ridership_YYYYMM.xlsx` (latest monthly OD workbook discovered on BART's ridership page)
- `data/bart/ridership/reference/station-names.xls` (station code reference)

## Parse monthly OD matrix to long format

```bash
./.venv/bin/python src/parse_bart_ridership_monthly.py
```

This writes `output/bart/ridership/od_long_YYYYMM.csv` with one row per origin/destination pair and day type.

## Assign weekday OD riders to edges

```bash
./.venv/bin/python src/assign_bart_od_to_edges.py
```

This writes:

- `output/bart/ridership/station_code_crosswalk_YYYYMM.csv`
- `output/bart/ridership/od_routed_weekday_YYYYMM.csv`
- `output/bart/ridership/edge_riders_weekday_YYYYMM.csv`

## Render weekday rider flow map

```bash
./.venv/bin/python src/plot_flow_map_weekday_riders.py
```

This writes `output/bart/flow_map_weekday_riders.png` with edge width scaled by assigned weekday riders.

## Render weekday rider flow on GTFS shapes

```bash
./.venv/bin/python src/plot_flow_map_weekday_riders_shapes.py
```

This writes:

- `output/bart/flow_map_weekday_riders_shapes.png`
- `output/bart/ridership/shape_segment_riders_weekday_202606.csv`

## Next pipeline steps

1. Parse trip-to-route relationships.
2. Confirm stop sequence semantics.
3. Derive station-level groupings from platform stops.
4. Build adjacent station pairs for graph construction.
