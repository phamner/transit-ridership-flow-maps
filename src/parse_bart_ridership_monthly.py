from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


RIDERSHIP_MONTHLY_DIR = Path("data/bart/ridership/monthly")
STATION_CODES_PATH = Path("data/bart/ridership/reference/station-names.xls")
OUTPUT_DIR = Path("output/bart/ridership")

FILE_PATTERN = re.compile(r"Ridership_(\d{6})\.xlsx$")
CODE_PATTERN = re.compile(r"^[A-Z0-9]{2}$")
PERIOD_PATTERN = re.compile(r"(\d{4})\s*/\s*(\d{2})")

DAY_TYPE_BY_SHEET = {
    "Total Trips": "total",
    "Average Weekday": "average_weekday",
    "Average Saturday": "average_saturday",
    "Average Sunday": "average_sunday",
}


def latest_monthly_workbook() -> tuple[Path, str]:
    candidates: list[tuple[str, Path]] = []

    for path in RIDERSHIP_MONTHLY_DIR.glob("Ridership_*.xlsx"):
        match = FILE_PATTERN.search(path.name)
        if match:
            candidates.append((match.group(1), path))

    if not candidates:
        raise FileNotFoundError(
            f"No monthly workbook found in {RIDERSHIP_MONTHLY_DIR}. Run download_bart_ridership.py first."
        )

    period, workbook = max(candidates, key=lambda item: item[0])
    return workbook, period


def parse_period_from_sheet(raw_sheet: pd.DataFrame, fallback_period: str) -> str:
    for value in raw_sheet.to_numpy().flatten():
        if isinstance(value, str):
            match = PERIOD_PATTERN.search(value)
            if match:
                return f"{match.group(1)}{match.group(2)}"

    return fallback_period


def find_header_row(raw_sheet: pd.DataFrame) -> int:
    first_col = raw_sheet.iloc[:, 0].astype(str)
    matches = first_col.str.contains("Exit Station Two-Letter Code", na=False)
    if not matches.any():
        raise ValueError("Could not locate matrix header row with station destination codes.")

    return int(matches[matches].index[0])


def destination_codes(header_row: pd.Series) -> list[tuple[int, str]]:
    destinations: list[tuple[int, str]] = []

    for col_idx, value in header_row.items():
        if col_idx == 0 or pd.isna(value):
            continue

        code = str(value).strip().upper()
        if CODE_PATTERN.fullmatch(code):
            destinations.append((int(col_idx), code))

    if not destinations:
        raise ValueError("No destination station codes found in matrix header row.")

    return destinations


def parse_sheet(workbook: Path, sheet_name: str, fallback_period: str) -> tuple[pd.DataFrame, str]:
    raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None)

    period = parse_period_from_sheet(raw, fallback_period)
    header_row_idx = find_header_row(raw)
    destination_meta = destination_codes(raw.iloc[header_row_idx])

    keep_columns = [0] + [col_idx for col_idx, _ in destination_meta]
    frame = raw.iloc[header_row_idx + 1 :, keep_columns].copy()
    frame.columns = ["origin_code"] + [code for _, code in destination_meta]

    frame["origin_code"] = frame["origin_code"].astype(str).str.strip().str.upper()
    frame = frame[frame["origin_code"].str.fullmatch(CODE_PATTERN, na=False)].copy()

    value_columns = [code for _, code in destination_meta]
    frame[value_columns] = frame[value_columns].apply(pd.to_numeric, errors="coerce")

    long_frame = frame.melt(
        id_vars="origin_code",
        value_vars=value_columns,
        var_name="destination_code",
        value_name="riders",
    ).dropna(subset=["riders"])

    long_frame["sheet_name"] = sheet_name
    long_frame["day_type"] = DAY_TYPE_BY_SHEET.get(sheet_name, "unknown")
    long_frame["period"] = period
    long_frame["is_intrastation"] = long_frame["origin_code"] == long_frame["destination_code"]

    return long_frame.reset_index(drop=True), period


def load_station_lookup() -> pd.DataFrame:
    station_reference = pd.read_excel(STATION_CODES_PATH)

    code_column = None
    name_column = None
    for column in station_reference.columns:
        label = str(column).strip().lower()
        if "two-letter station code" in label:
            code_column = column
        if "station name" in label:
            name_column = column

    if code_column is None or name_column is None:
        raise ValueError("Could not identify station code and station name columns in station-names.xls")

    lookup = station_reference[[code_column, name_column]].copy()
    lookup.columns = ["station_code", "station_name"]
    lookup["station_code"] = lookup["station_code"].astype(str).str.strip().str.upper()
    lookup["station_name"] = lookup["station_name"].astype(str).str.strip()
    lookup = lookup[lookup["station_code"].str.fullmatch(CODE_PATTERN, na=False)]

    return lookup.drop_duplicates(subset=["station_code"]).reset_index(drop=True)


def main() -> None:
    workbook, fallback_period = latest_monthly_workbook()
    print(f"Using workbook: {workbook}")

    all_rows: list[pd.DataFrame] = []
    periods: set[str] = set()

    for sheet_name in DAY_TYPE_BY_SHEET:
        parsed_sheet, period = parse_sheet(workbook, sheet_name, fallback_period=fallback_period)
        all_rows.append(parsed_sheet)
        periods.add(period)

    od_long = pd.concat(all_rows, ignore_index=True)

    if len(periods) == 1:
        period = next(iter(periods))
    else:
        period = fallback_period

    station_lookup = load_station_lookup()

    origin_lookup = station_lookup.rename(
        columns={"station_code": "origin_code", "station_name": "origin_station_name"}
    )
    destination_lookup = station_lookup.rename(
        columns={"station_code": "destination_code", "station_name": "destination_station_name"}
    )

    od_long = od_long.merge(origin_lookup, on="origin_code", how="left")
    od_long = od_long.merge(destination_lookup, on="destination_code", how="left")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"od_long_{period}.csv"
    od_long.to_csv(output_path, index=False)

    unique_codes = sorted(set(od_long["origin_code"]).union(set(od_long["destination_code"])))
    mapped_codes = set(station_lookup["station_code"])
    missing_codes = [code for code in unique_codes if code not in mapped_codes]

    print(f"Wrote {len(od_long):,} OD rows to {output_path}")
    print(f"Detected period: {period}")
    print(f"Unique station codes in OD matrix: {len(unique_codes)}")
    print(f"Missing station code mappings: {len(missing_codes)}")

    if missing_codes:
        print(f"Missing codes: {', '.join(missing_codes)}")

    print()
    print("Rows by day_type")
    by_day = od_long.groupby("day_type", as_index=False).agg(rows=("riders", "size"), total_riders=("riders", "sum"))
    print(by_day.to_string(index=False))


if __name__ == "__main__":
    main()
