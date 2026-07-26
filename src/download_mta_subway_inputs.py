from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import urllib.parse
import zipfile
from math import ceil
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from agency_config import get_agency_config


MTA_SUBWAY_GTFS_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"
MTA_OD_DATASET_ID = "28vm-gjqr"
SOCRATA_BASE = "https://data.ny.gov/resource"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

PERIOD_PATTERN = re.compile(r"20\d{2}(0[1-9]|1[0-2])")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download latest MTA NYC Subway GTFS and monthly OD ridership estimate data."
    )
    parser.add_argument("--agency", default="mta_subway", help="Agency id from agency_config.py")
    parser.add_argument("--gtfs-only", action="store_true", help="Download only GTFS")
    parser.add_argument("--ridership-only", action="store_true", help="Download only ridership OD data")
    parser.add_argument("--skip-gtfs-extract", action="store_true", help="Keep GTFS zip only, do not extract .txt files")
    parser.add_argument("--period", help="Optional YYYYMM period override for OD download")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=250000,
        help="Rows per Socrata page for OD download (default: 250000)",
    )
    return parser.parse_args()


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers=REQUEST_HEADERS)
    with urlopen(request) as response:
        return response.read()


def validate_period(period: str) -> str:
    if not PERIOD_PATTERN.fullmatch(period):
        raise ValueError(f"Invalid period '{period}'. Expected YYYYMM.")
    return period


def resolve_latest_period() -> str:
    params = {
        "$select": "distinct year,month",
        "$order": "year DESC,month DESC",
        "$limit": "1",
    }
    query = urllib.parse.urlencode(params)
    url = f"{SOCRATA_BASE}/{MTA_OD_DATASET_ID}.json?{query}"
    payload = fetch_bytes(url).decode("utf-8", errors="ignore")
    rows = json.loads(payload)

    if not rows:
        raise RuntimeError("Could not determine latest MTA OD period from Socrata API.")

    row = rows[0]
    year = int(row["year"])
    month = int(row["month"])
    return f"{year:04d}{month:02d}"


def period_to_year_month(period: str) -> tuple[int, int]:
    period = validate_period(period)
    return int(period[:4]), int(period[4:6])


def resolve_monthly_row_count(period: str) -> int:
    year, month = period_to_year_month(period)
    params = {
        "$select": "count(1) as n",
        "$where": f"year={year} AND month={month}",
    }
    query = urllib.parse.urlencode(params)
    url = f"{SOCRATA_BASE}/{MTA_OD_DATASET_ID}.json?{query}"
    payload = fetch_bytes(url).decode("utf-8", errors="ignore")
    rows = json.loads(payload)
    if not rows:
        return 0
    return int(rows[0].get("n", 0))


def download_gtfs(cfg, skip_extract: bool) -> tuple[Path, int]:
    cfg.gtfs_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cfg.gtfs_dir.parent / "google_transit.zip"

    gtfs_bytes = fetch_bytes(MTA_SUBWAY_GTFS_URL)
    with zipfile.ZipFile(io.BytesIO(gtfs_bytes)):
        pass

    zip_path.write_bytes(gtfs_bytes)

    extracted_count = 0
    if not skip_extract:
        for existing in cfg.gtfs_dir.glob("*.txt"):
            existing.unlink()

        with zipfile.ZipFile(io.BytesIO(gtfs_bytes)) as zf:
            for member in zf.namelist():
                if member.endswith(".txt"):
                    filename = Path(member).name
                    with zf.open(member) as src, (cfg.gtfs_dir / filename).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted_count += 1

    return zip_path, extracted_count


def build_monthly_od_url(period: str, limit: int, offset: int) -> str:
    year, month = period_to_year_month(period)

    params = {
        "$select": ",".join(
            [
                "year",
                "month",
                "day_of_week",
                "origin_station_complex_id",
                "origin_station_complex_name",
                "destination_station_complex_id",
                "destination_station_complex_name",
                "estimated_average_ridership",
            ]
        ),
        "$where": f"year={year} AND month={month}",
        "$limit": str(limit),
        "$offset": str(offset),
    }

    return f"{SOCRATA_BASE}/{MTA_OD_DATASET_ID}.csv?{urllib.parse.urlencode(params)}"


def download_ridership(cfg, period_override: str | None, chunk_size: int) -> dict[str, Path]:
    cfg.ridership_monthly_dir.mkdir(parents=True, exist_ok=True)

    if chunk_size <= 0:
        raise ValueError("--chunk-size must be a positive integer")

    period = validate_period(period_override) if period_override else resolve_latest_period()
    expected_rows = resolve_monthly_row_count(period)
    if expected_rows <= 0:
        raise RuntimeError(f"No OD rows returned for period {period}")

    raw_output = cfg.ridership_monthly_dir / f"mta_subway_od_raw_{period}.csv"

    total_downloaded = 0
    first_page = True
    max_pages = ceil(expected_rows / chunk_size)

    for page_idx, offset in enumerate(range(0, expected_rows, chunk_size), start=1):
        od_url = build_monthly_od_url(period=period, limit=chunk_size, offset=offset)
        page_csv = fetch_bytes(od_url).decode("utf-8", errors="ignore")
        page_frame = pd.read_csv(io.StringIO(page_csv))
        page_rows = len(page_frame)

        if page_rows == 0:
            break

        page_frame.to_csv(raw_output, mode="w" if first_page else "a", index=False, header=first_page)
        first_page = False
        total_downloaded += page_rows

        print(f"Downloaded page {page_idx}/{max_pages} ({page_rows:,} rows, total {total_downloaded:,})")

        if page_rows < chunk_size:
            break

    frame = pd.read_csv(raw_output, usecols=["origin_station_complex_id", "destination_station_complex_id"])
    summary = pd.DataFrame(
        [
            {
                "period": period,
                "row_count": total_downloaded,
                "expected_row_count": expected_rows,
                "distinct_origin_complexes": frame["origin_station_complex_id"].nunique(dropna=True),
                "distinct_destination_complexes": frame["destination_station_complex_id"].nunique(dropna=True),
                "source": "MTA / data.ny.gov Subway Origin-Destination Ridership Estimate",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )

    summary_output = cfg.ridership_monthly_dir / f"ridership_summary_{period}.csv"
    summary.to_csv(summary_output, index=False)

    return {
        "od_raw": raw_output,
        "summary": summary_output,
    }


def main() -> None:
    args = parse_args()
    cfg = get_agency_config(args.agency)

    if args.gtfs_only and args.ridership_only:
        raise ValueError("Choose only one of --gtfs-only or --ridership-only")

    run_gtfs = not args.ridership_only
    run_ridership = not args.gtfs_only

    if run_gtfs:
        gtfs_zip, extracted = download_gtfs(cfg, skip_extract=args.skip_gtfs_extract)
        print(f"Downloaded MTA Subway GTFS zip: {gtfs_zip}")
        if args.skip_gtfs_extract:
            print("Skipped GTFS extraction (--skip-gtfs-extract)")
        else:
            print(f"Extracted GTFS text files into {cfg.gtfs_dir}: {extracted} files")

    if run_ridership:
        outputs = download_ridership(cfg, period_override=args.period, chunk_size=args.chunk_size)
        print("Downloaded MTA Subway OD inputs:")
        for key in sorted(outputs.keys()):
            print(f"- {key}: {outputs[key]}")

        summary = pd.read_csv(outputs["summary"]).iloc[0]
        print()
        print(f"Latest period: {summary['period']}")
        print(f"Rows in OD extract: {int(summary['row_count']):,}")
        print(f"Expected rows for period: {int(summary['expected_row_count']):,}")
        print(f"Distinct origin complexes: {int(summary['distinct_origin_complexes']):,}")
        print(f"Distinct destination complexes: {int(summary['distinct_destination_complexes']):,}")


if __name__ == "__main__":
    main()
