from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from agency_config import get_agency_config


PERIOD_PATTERN = re.compile(r"(20\d{2})(0[1-9]|1[0-2])")
WEEKDAY_NAMES = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
SATURDAY_NAME = "Saturday"
SUNDAY_NAME = "Sunday"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse MTA NYC Subway monthly OD estimate into od_long_YYYYMM.csv."
    )
    parser.add_argument("--agency", default="mta_subway", help="Agency id from agency_config.py")
    parser.add_argument("--input", help="Input CSV path. If omitted, newest mta_subway_od_raw_YYYYMM.csv is used.")
    parser.add_argument("--period", help="Optional YYYYMM period override/filter.")
    parser.add_argument("--output-dir", help="Override output ridership directory")
    parser.add_argument("--chunk-size", type=int, default=300000, help="CSV read chunk size (default: 300000)")
    return parser.parse_args()


def latest_monthly_file(monthly_dir: Path) -> Path:
    candidates = sorted(monthly_dir.glob("mta_subway_od_raw_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No MTA raw OD files found in {monthly_dir}. Run download_mta_subway_inputs.py first."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def detect_period(file_path: Path, override: str | None) -> str:
    if override:
        if not PERIOD_PATTERN.fullmatch(override):
            raise ValueError("--period must be YYYYMM")
        return override

    match = PERIOD_PATTERN.search(file_path.stem)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    raise ValueError("Could not detect period from filename. Pass --period YYYYMM.")


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = frame.rename(
        columns={
            "Year": "year",
            "Month": "month",
            "Day of Week": "day_of_week",
            "Hour of Day": "hour_of_day",
            "Origin Station Complex ID": "origin_station_complex_id",
            "Origin Station Complex Name": "origin_station_complex_name",
            "Destination Station Complex ID": "destination_station_complex_id",
            "Destination Station Complex Name": "destination_station_complex_name",
            "Estimated Average Ridership": "estimated_average_ridership",
        }
    )
    return renamed


def clean_station_name(name: str) -> str:
    text = str(name).strip()
    # Strip route bullets in trailing parentheses, e.g. "8 St-NYU (R,W)".
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def aggregate_chunk_to_keyed_totals(chunk: pd.DataFrame, period: str) -> pd.DataFrame:
    required = {
        "year",
        "month",
        "day_of_week",
        "origin_station_complex_id",
        "origin_station_complex_name",
        "destination_station_complex_id",
        "destination_station_complex_name",
    }
    missing = [col for col in required if col not in chunk.columns]
    if missing:
        raise ValueError(f"Input missing required columns: {missing}")

    riders_column = "riders_daily" if "riders_daily" in chunk.columns else "estimated_average_ridership"
    if riders_column not in chunk.columns:
        raise ValueError("Input must include either 'riders_daily' or 'estimated_average_ridership'.")

    year = int(period[:4])
    month = int(period[4:6])

    working = normalize_columns(chunk)
    working["year"] = pd.to_numeric(working["year"], errors="coerce")
    working["month"] = pd.to_numeric(working["month"], errors="coerce")
    working = working[(working["year"] == year) & (working["month"] == month)].copy()

    if working.empty:
        return pd.DataFrame(
            columns=[
                "day_of_week",
                "origin_code",
                "destination_code",
                "origin_station_name",
                "destination_station_name",
                "riders",
            ]
        )

    working["riders"] = pd.to_numeric(working[riders_column], errors="coerce")
    working = working.dropna(
        subset=[
            "day_of_week",
            "origin_station_complex_id",
            "origin_station_complex_name",
            "destination_station_complex_id",
            "destination_station_complex_name",
            "riders",
        ]
    ).copy()

    working["day_of_week"] = working["day_of_week"].astype(str).str.strip()
    working["origin_code"] = "NYC" + working["origin_station_complex_id"].astype(str).str.strip()
    working["destination_code"] = "NYC" + working["destination_station_complex_id"].astype(str).str.strip()
    working["origin_station_name"] = (
        working["origin_station_complex_name"].astype(str).str.replace(r"\s*\([^)]*\)\s*$", "", regex=True).str.strip()
    )
    working["destination_station_name"] = (
        working["destination_station_complex_name"].astype(str).str.replace(r"\s*\([^)]*\)\s*$", "", regex=True).str.strip()
    )

    return (
        working.groupby(
            [
                "day_of_week",
                "origin_code",
                "destination_code",
                "origin_station_name",
                "destination_station_name",
            ],
            as_index=False,
        )
        .agg(riders=("riders", "sum"))
    )


def compute_day_type_rows(by_dow_total: pd.DataFrame, period: str) -> pd.DataFrame:

    weekday = (
        by_dow_total[by_dow_total["day_of_week"].isin(WEEKDAY_NAMES)]
        .groupby([
            "origin_code",
            "destination_code",
            "origin_station_name",
            "destination_station_name",
        ], as_index=False)
        .agg(riders=("riders", "mean"))
    )
    weekday["day_type"] = "average_weekday"

    saturday = by_dow_total[by_dow_total["day_of_week"] == SATURDAY_NAME].copy()
    saturday["day_type"] = "average_saturday"

    sunday = by_dow_total[by_dow_total["day_of_week"] == SUNDAY_NAME].copy()
    sunday["day_type"] = "average_sunday"

    combined = pd.concat([weekday, saturday, sunday], ignore_index=True)
    combined["sheet_name"] = "mta_subway_od_estimate"
    combined["period"] = period
    combined["is_intrastation"] = combined["origin_code"] == combined["destination_code"]

    return combined[
        [
            "origin_code",
            "destination_code",
            "riders",
            "sheet_name",
            "day_type",
            "period",
            "is_intrastation",
            "origin_station_name",
            "destination_station_name",
        ]
    ].sort_values(["day_type", "origin_code", "destination_code"], kind="stable")


def main() -> None:
    args = parse_args()
    cfg = get_agency_config(args.agency)

    input_path = Path(args.input) if args.input else latest_monthly_file(cfg.ridership_monthly_dir)
    period = detect_period(input_path, args.period)

    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be a positive integer")

    totals_by_key: dict[tuple[str, str, str, str, str], float] = defaultdict(float)
    chunk_count = 0
    row_count = 0

    for chunk in pd.read_csv(input_path, chunksize=args.chunk_size):
        chunk_count += 1
        row_count += len(chunk)
        grouped = aggregate_chunk_to_keyed_totals(chunk, period=period)
        for row in grouped.itertuples(index=False):
            key = (
                str(row.day_of_week),
                str(row.origin_code),
                str(row.destination_code),
                str(row.origin_station_name),
                str(row.destination_station_name),
            )
            totals_by_key[key] += float(row.riders)

        if chunk_count % 5 == 0:
            print(f"Processed {chunk_count} chunks ({row_count:,} rows)")

    by_dow_total = pd.DataFrame(
        [
            {
                "day_of_week": key[0],
                "origin_code": key[1],
                "destination_code": key[2],
                "origin_station_name": key[3],
                "destination_station_name": key[4],
                "riders": value,
            }
            for key, value in totals_by_key.items()
        ]
    )

    od_long = compute_day_type_rows(by_dow_total, period=period)

    output_dir = Path(args.output_dir) if args.output_dir else cfg.ridership_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"od_long_{period}.csv"
    od_long.to_csv(output_path, index=False)

    station_lookup_out = (
        pd.concat(
            [
                od_long[["origin_code", "origin_station_name"]].rename(
                    columns={"origin_code": "station_code", "origin_station_name": "station_name"}
                ),
                od_long[["destination_code", "destination_station_name"]].rename(
                    columns={"destination_code": "station_code", "destination_station_name": "station_name"}
                ),
            ],
            ignore_index=True,
        )
        .drop_duplicates()
        .sort_values(["station_code", "station_name"])
        .reset_index(drop=True)
    )
    lookup_out_path = output_dir / f"station_code_lookup_{period}.csv"
    station_lookup_out.to_csv(lookup_out_path, index=False)

    print(f"Using input file: {input_path}")
    print(f"Source rows read: {row_count:,}")
    print(f"Wrote OD long file: {output_path}")
    print(f"Wrote station lookup: {lookup_out_path}")
    print(f"OD rows: {len(od_long):,}")
    print(f"Unique stations: {len(station_lookup_out):,}")

    summary = (
        od_long.groupby("day_type", as_index=False)
        .agg(rows=("riders", "size"), total_riders=("riders", "sum"))
        .sort_values("day_type")
    )
    print()
    print("Rows by day_type")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
