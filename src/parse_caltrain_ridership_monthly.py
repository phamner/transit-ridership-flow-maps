from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import pandas as pd

from agency_config import get_agency_config


PERIOD_PATTERN = re.compile(r"(20\d{2})(0[1-9]|1[0-2])")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse Caltrain OD ridership file into od_long_YYYYMM.csv for flow-map assignment."
    )
    parser.add_argument("--agency", default="caltrain", help="Agency id from agency_config.py")
    parser.add_argument("--input", help="Input OD file path (.csv or .xlsx). If omitted, newest monthly file is used.")
    parser.add_argument("--sheet", help="Excel sheet name for .xlsx input (defaults to first sheet).")
    parser.add_argument("--period", help="Override period in YYYYMM format.")
    parser.add_argument("--origin-column", default="origin_station_name", help="Column containing origin station name")
    parser.add_argument(
        "--destination-column",
        default="destination_station_name",
        help="Column containing destination station name",
    )
    parser.add_argument("--riders-column", default="average_weekday_riders", help="Column containing rider counts")
    parser.add_argument("--origin-code-column", help="Optional origin station code column")
    parser.add_argument("--destination-code-column", help="Optional destination station code column")
    parser.add_argument(
        "--day-type",
        default="average_weekday",
        choices=["total", "average_weekday", "average_saturday", "average_sunday"],
        help="Day-type label to write into od_long output",
    )
    parser.add_argument(
        "--station-lookup",
        help=(
            "Optional station lookup CSV with station_name and station_code columns. "
            "If omitted, parser checks data/<agency>/ridership/reference/station_codes.csv."
        ),
    )
    parser.add_argument("--station-name-column", default="station_name", help="Station lookup name column")
    parser.add_argument("--station-code-column", default="station_code", help="Station lookup code column")
    parser.add_argument("--output-dir", help="Override output ridership directory")
    return parser.parse_args()


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", ascii_name)


def latest_monthly_file(monthly_dir: Path) -> Path:
    candidates = sorted(monthly_dir.glob("*"))
    candidates = [path for path in candidates if path.suffix.lower() in {".csv", ".xlsx", ".xls"}]
    if not candidates:
        raise FileNotFoundError(
            f"No ridership files found in {monthly_dir}. Add a CSV/XLSX file or pass --input explicitly."
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


def read_input(path: Path, sheet: str | None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=sheet) if sheet else pd.read_excel(path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def load_station_lookup(
    lookup_path: Path,
    station_name_column: str,
    station_code_column: str,
) -> pd.DataFrame:
    lookup = pd.read_csv(lookup_path)

    if station_name_column not in lookup.columns or station_code_column not in lookup.columns:
        raise ValueError(
            f"Lookup missing columns. Required: {station_name_column}, {station_code_column}. "
            f"Available: {list(lookup.columns)}"
        )

    lookup = lookup[[station_name_column, station_code_column]].copy()
    lookup.columns = ["station_name", "station_code"]
    lookup["station_name"] = lookup["station_name"].astype(str).str.strip()
    lookup["station_code"] = lookup["station_code"].astype(str).str.strip()
    lookup = lookup[(lookup["station_name"] != "") & (lookup["station_code"] != "")]
    lookup["station_name_norm"] = lookup["station_name"].map(normalize_name)
    return lookup.drop_duplicates(subset=["station_name_norm"]).reset_index(drop=True)


def generate_station_codes(od_frame: pd.DataFrame) -> pd.DataFrame:
    unique_names = sorted(
        set(od_frame["origin_station_name"].dropna().astype(str).str.strip())
        | set(od_frame["destination_station_name"].dropna().astype(str).str.strip())
    )
    rows = []
    for idx, station_name in enumerate(unique_names, start=1):
        rows.append(
            {
                "station_name": station_name,
                "station_code": f"CT{idx:03d}",
                "station_name_norm": normalize_name(station_name),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    cfg = get_agency_config(args.agency)

    input_path = Path(args.input) if args.input else latest_monthly_file(cfg.ridership_monthly_dir)
    period = detect_period(input_path, args.period)

    raw = read_input(input_path, args.sheet)

    required_columns = [args.origin_column, args.destination_column, args.riders_column]
    missing = [col for col in required_columns if col not in raw.columns]
    if missing:
        raise ValueError(f"Input missing required columns: {missing}. Available: {list(raw.columns)}")

    od = raw[[args.origin_column, args.destination_column, args.riders_column]].copy()
    od.columns = ["origin_station_name", "destination_station_name", "riders"]

    od["origin_station_name"] = od["origin_station_name"].astype(str).str.strip()
    od["destination_station_name"] = od["destination_station_name"].astype(str).str.strip()
    od["riders"] = pd.to_numeric(od["riders"], errors="coerce")

    od = od.dropna(subset=["origin_station_name", "destination_station_name", "riders"]).copy()
    od = od[(od["origin_station_name"] != "") & (od["destination_station_name"] != "")]
    od = od[od["riders"] >= 0].reset_index(drop=True)

    if args.origin_code_column and args.destination_code_column:
        if args.origin_code_column not in raw.columns or args.destination_code_column not in raw.columns:
            raise ValueError("Configured code columns were not found in input file.")
        code_frame = raw[[args.origin_code_column, args.destination_code_column]].copy()
        code_frame.columns = ["origin_code", "destination_code"]
        code_frame["origin_code"] = code_frame["origin_code"].astype(str).str.strip()
        code_frame["destination_code"] = code_frame["destination_code"].astype(str).str.strip()
        od = pd.concat([od, code_frame], axis=1)
    else:
        lookup_path = Path(args.station_lookup) if args.station_lookup else (cfg.ridership_reference_dir / "station_codes.csv")

        if lookup_path.exists():
            station_lookup = load_station_lookup(
                lookup_path=lookup_path,
                station_name_column=args.station_name_column,
                station_code_column=args.station_code_column,
            )
        else:
            station_lookup = generate_station_codes(od)

        origin_lookup = station_lookup[["station_name_norm", "station_code"]].rename(
            columns={"station_name_norm": "origin_norm", "station_code": "origin_code"}
        )
        destination_lookup = station_lookup[["station_name_norm", "station_code"]].rename(
            columns={"station_name_norm": "destination_norm", "station_code": "destination_code"}
        )

        od["origin_norm"] = od["origin_station_name"].map(normalize_name)
        od["destination_norm"] = od["destination_station_name"].map(normalize_name)
        od = od.merge(origin_lookup, on="origin_norm", how="left")
        od = od.merge(destination_lookup, on="destination_norm", how="left")

        missing_codes = od[od["origin_code"].isna() | od["destination_code"].isna()]
        if not missing_codes.empty:
            examples = (
                missing_codes[["origin_station_name", "destination_station_name"]]
                .head(10)
                .to_dict(orient="records")
            )
            raise ValueError(f"Could not map station codes for some OD rows. Examples: {examples}")

        od = od.drop(columns=["origin_norm", "destination_norm"])

    od_long = od.copy()
    od_long["sheet_name"] = "caltrain_input"
    od_long["day_type"] = args.day_type
    od_long["period"] = period
    od_long["is_intrastation"] = od_long["origin_code"] == od_long["destination_code"]

    ordered_columns = [
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
    od_long = od_long[ordered_columns].sort_values(
        ["origin_code", "destination_code"],
        kind="stable",
    )

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
                    columns={
                        "destination_code": "station_code",
                        "destination_station_name": "station_name",
                    }
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
    print(f"Wrote OD long file: {output_path}")
    print(f"Wrote station lookup: {lookup_out_path}")
    print(f"OD rows: {len(od_long):,}")
    print(f"Unique stations: {len(station_lookup_out):,}")
    print(f"Total riders ({args.day_type}): {od_long['riders'].sum():,.2f}")


if __name__ == "__main__":
    main()