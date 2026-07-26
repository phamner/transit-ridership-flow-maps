# Transit Ridership Flow Maps

Open-source transit analysis pipeline for producing Subway Builder-style ridership flow maps from public GTFS and OD data.

## Current maps

### BART

![BART weekday rider flow map](output/bart/flow_map_weekday_riders_shapes.png)

### MTA NYC Subway (full network)

![MTA NYC Subway weekday rider flow map](output/mta_subway/flow_map_weekday_riders_shapes.png)

### MTA NYC Subway (Manhattan only)

![MTA NYC Subway Manhattan weekday rider flow map](output/mta_subway/flow_map_weekday_riders_shapes_manhattan.png)

## Current focus

The first milestone is a clean GTFS inspection layer for BART. The pipeline is now agency-config driven so the same scripts can be reused for additional transit systems.

This repository currently includes:

- a lightweight GTFS feed loader
- a script that inspects stops, trips, and station aggregation
- the BART GTFS feed under `data/bart/gtfs/current/`
- shared agency config in `src/agency_config.py`

## Multi-agency structure

Core scripts now read defaults from `src/agency_config.py`.

- Add a new agency by adding one entry to `AGENCY_CONFIGS`
- Point that entry to the agency's GTFS and output directories
- Add any OD station-name aliases needed for crosswalk matching

Current generic scripts (still BART-compatible):

- `src/build_edge_table.py --agency <agency_id>`
- `src/assign_bart_od_to_edges.py --agency <agency_id>`
- `src/plot_flow_map_weekday_riders_shapes.py --agency <agency_id>`

These scripts can also accept explicit input paths if needed.

## Run the inspection script

```bash
./.venv/bin/python src/read_gtfs.py
```

## Build station edge tables

```bash
./.venv/bin/python src/build_edge_table.py --agency bart
```

This writes:

- `output/bart/trip_station_pairs.csv`: directional station-to-station traversals by trip
- `output/bart/physical_edges.csv`: de-duplicated physical station pairs with traversal counts

For another agency, pass its id in config:

```bash
./.venv/bin/python src/build_edge_table.py --agency caltrain
```

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
./.venv/bin/python src/assign_bart_od_to_edges.py --agency bart
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
PYTHONPATH=src ./.venv/bin/python src/plot_flow_map_weekday_riders_shapes.py --agency bart
```

This writes:

- `output/bart/flow_map_weekday_riders_shapes.png`
- `output/bart/ridership/shape_segment_riders_weekday_202606.csv`

## Next pipeline steps

1. Parse trip-to-route relationships.
2. Confirm stop sequence semantics.
3. Derive station-level groupings from platform stops.
4. Build adjacent station pairs for graph construction.

## Caltrain quick start

The repository now includes a `caltrain` entry in `src/agency_config.py`.

To run the same flow-map pipeline for Caltrain:

1. Download latest Caltrain GTFS and latest published ridership tables:

```bash
./.venv/bin/python src/download_caltrain_inputs.py
```

This writes:

- `data/caltrain/gtfs/current/*.txt` from the latest GTFS zip
- `data/caltrain/ridership/monthly/ridership_summary_YYYYMM.csv` with latest total and average weekday ridership
- raw/exported Tableau ridership tables under `data/caltrain/ridership/monthly/`

Optional:

- GTFS only: `./.venv/bin/python src/download_caltrain_inputs.py --gtfs-only`
- Ridership only: `./.venv/bin/python src/download_caltrain_inputs.py --ridership-only`

2. Parse Caltrain OD input to OD long format:

```bash
./.venv/bin/python src/parse_caltrain_ridership_monthly.py --input data/caltrain/ridership/monthly/YOUR_FILE.csv --period 202606
```

Default expected columns are:

- `origin_station_name`
- `destination_station_name`
- `average_weekday_riders`

If your source file uses different names, pass `--origin-column`, `--destination-column`, and `--riders-column`.

Optional station-code lookup:

- Put `station_codes.csv` in `data/caltrain/ridership/reference/`
- Required columns: `station_name`, `station_code`
- If no lookup is present, synthetic codes (`CT001`, `CT002`, ...) are generated automatically.

3. Build physical edges:

```bash
./.venv/bin/python src/build_edge_table.py --agency caltrain
```

4. Assign weekday OD riders to edges:

```bash
./.venv/bin/python src/assign_bart_od_to_edges.py --agency caltrain
```

5. Render shape-based rider map:

```bash
PYTHONPATH=src ./.venv/bin/python src/plot_flow_map_weekday_riders_shapes.py --agency caltrain
```

Expected Caltrain outputs:

- `output/caltrain/ridership/od_long_YYYYMM.csv`
- `output/caltrain/physical_edges.csv`
- `output/caltrain/ridership/edge_riders_weekday_YYYYMM.csv`
- `output/caltrain/flow_map_weekday_riders_shapes.png`

## MTA NYC Subway quick start

The repository now includes an `mta_subway` entry in `src/agency_config.py`.

To run the same flow-map pipeline for MTA NYC Subway:

1. Download latest MTA Subway GTFS and latest available monthly OD extract:

```bash
./.venv/bin/python src/download_mta_subway_inputs.py
```

This writes:

- `data/mta_subway/gtfs/current/*.txt`
- `data/mta_subway/ridership/monthly/mta_subway_od_raw_YYYYMM.csv`
- `data/mta_subway/ridership/monthly/ridership_summary_YYYYMM.csv`

Optional:

- GTFS only: `./.venv/bin/python src/download_mta_subway_inputs.py --gtfs-only`
- Ridership only: `./.venv/bin/python src/download_mta_subway_inputs.py --ridership-only`

2. Parse monthly MTA OD estimate into `od_long_YYYYMM.csv`:

```bash
./.venv/bin/python src/parse_mta_subway_od_monthly.py
```

Notes:

- Output includes `average_weekday`, `average_saturday`, and `average_sunday` rows.
- The source is an official modeled OD estimate (not direct tap-out observations).

3. Build physical edges:

```bash
./.venv/bin/python src/build_edge_table.py --agency mta_subway
```

4. Assign weekday OD riders to edges:

```bash
./.venv/bin/python src/assign_bart_od_to_edges.py --agency mta_subway
```

5. Render shape-based rider map:

```bash
PYTHONPATH=src ./.venv/bin/python src/plot_flow_map_weekday_riders_shapes.py --agency mta_subway
```

Expected MTA outputs:

- `output/mta_subway/ridership/od_long_YYYYMM.csv`
- `output/mta_subway/physical_edges.csv`
- `output/mta_subway/ridership/edge_riders_weekday_YYYYMM.csv`
- `output/mta_subway/flow_map_weekday_riders_shapes.png`
