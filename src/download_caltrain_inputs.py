from __future__ import annotations

import argparse
import csv
import io
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from agency_config import get_agency_config


CALTRAIN_GTFS_URLS = [
    "https://data.trilliumtransit.com/gtfs/caltrain-ca-us/caltrain-ca-us.zip",
    "https://www.caltrain.com/Assets/GTFS/google_transit.zip",
]
TABLEAU_WORKBOOK_NAME = "CaltrainTotalRidershipEstimates"

RIDERSHIP_VIEWS = {
    "executive_summary": "ExecutiveSummary",
    "data_download": "DataDownload",
    "awr_by_fiscal_year": "AWRbyFiscalYear",
    "monthly_ridership_by_fiscal_year": "MonthlyRidershipbyFiscalYear",
}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "*/*",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download latest Caltrain GTFS and ridership inputs."
    )
    parser.add_argument("--agency", default="caltrain", help="Agency id from agency_config.py")
    parser.add_argument("--gtfs-only", action="store_true", help="Download only GTFS")
    parser.add_argument("--ridership-only", action="store_true", help="Download only ridership tables")
    parser.add_argument("--skip-gtfs-extract", action="store_true", help="Keep GTFS zip only, do not extract .txt files")
    parser.add_argument("--period", help="Optional YYYYMM override for output file naming")
    return parser.parse_args()


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers=REQUEST_HEADERS)
    with urlopen(request) as response:
        return response.read()


def download_gtfs(cfg, skip_extract: bool) -> tuple[Path, int]:
    cfg.gtfs_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cfg.gtfs_dir.parent / "google_transit.zip"

    gtfs_bytes = b""
    source_url = None
    errors: list[str] = []

    for url in CALTRAIN_GTFS_URLS:
        try:
            payload = fetch_bytes(url)
            with zipfile.ZipFile(io.BytesIO(payload)):
                pass
            gtfs_bytes = payload
            source_url = url
            break
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    if not gtfs_bytes:
        joined = " | ".join(errors)
        raise RuntimeError(f"Could not download a valid GTFS zip. Tried: {joined}")

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

    print(f"GTFS source URL: {source_url}")
    return zip_path, extracted_count


def fetch_tableau_csv(view_name: str) -> str:
    url = f"https://public.tableau.com/views/{TABLEAU_WORKBOOK_NAME}/{view_name}.csv?:showVizHome=no"
    return fetch_bytes(url).decode("utf-8", errors="ignore")


def parse_month_label(text: str) -> str | None:
    match = re.search(r"([A-Za-z]{3,9})\s+(20\d{2})", text)
    if not match:
        return None

    dt = pd.to_datetime(f"{match.group(1)} {match.group(2)}", format="%b %Y", errors="coerce")
    if pd.isna(dt):
        dt = pd.to_datetime(f"{match.group(1)} {match.group(2)}", format="%B %Y", errors="coerce")
    if pd.isna(dt):
        return None

    return dt.strftime("%Y%m")


def parse_fiscal_year(value: str) -> int | None:
    match = re.search(r"(20\d{2})", str(value))
    return int(match.group(1)) if match else None


def fiscal_month_to_period(month_name: str, fiscal_year_label: str) -> str | None:
    month_dt = pd.to_datetime(month_name, format="%B", errors="coerce")
    if pd.isna(month_dt):
        month_dt = pd.to_datetime(month_name, format="%b", errors="coerce")
    if pd.isna(month_dt):
        return None

    fy = parse_fiscal_year(fiscal_year_label)
    if fy is None:
        return None

    month = int(month_dt.month)
    calendar_year = fy - 1 if month >= 7 else fy
    return f"{calendar_year:04d}{month:02d}"


def latest_period_from_data_download(frame: pd.DataFrame) -> tuple[str, str]:
    if "Month, Year of Date" not in frame.columns:
        raise ValueError("DataDownload CSV missing 'Month, Year of Date' column")

    parsed = pd.to_datetime(frame["Month, Year of Date"], format="%B %Y", errors="coerce")
    if parsed.isna().all():
        raise ValueError("Could not parse Month, Year of Date values")

    latest_idx = parsed.idxmax()
    latest_dt = parsed.loc[latest_idx]
    return latest_dt.strftime("%Y%m"), latest_dt.strftime("%b %Y")


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def build_latest_summary(period: str, month_label: str, data_download: pd.DataFrame, awr_by_fy: pd.DataFrame) -> pd.DataFrame:
    if "Caltrain Ridership" not in data_download.columns:
        raise ValueError("DataDownload CSV missing 'Caltrain Ridership' column")

    data_download = data_download.copy()
    data_download["parsed_period"] = pd.to_datetime(
        data_download["Month, Year of Date"],
        format="%B %Y",
        errors="coerce",
    ).dt.strftime("%Y%m")
    data_download["caltrain_ridership_numeric"] = to_numeric(data_download["Caltrain Ridership"])

    latest_total = data_download.loc[
        data_download["parsed_period"] == period,
        "caltrain_ridership_numeric",
    ].dropna()

    total_ridership = float(latest_total.iloc[-1]) if not latest_total.empty else float("nan")

    awr_value = float("nan")
    if {"Month of Fiscal Date", "Year of Fiscal Date", "Average Weekday Ridership"}.issubset(awr_by_fy.columns):
        awr = awr_by_fy.copy()
        awr["parsed_period"] = awr.apply(
            lambda row: fiscal_month_to_period(row["Month of Fiscal Date"], row["Year of Fiscal Date"]),
            axis=1,
        )
        awr["awr_numeric"] = to_numeric(awr["Average Weekday Ridership"])
        latest_awr = awr.loc[awr["parsed_period"] == period, "awr_numeric"].dropna()
        if not latest_awr.empty:
            awr_value = float(latest_awr.iloc[-1])

    summary = pd.DataFrame(
        [
            {
                "period": period,
                "month_label": month_label,
                "total_ridership": total_ridership,
                "average_weekday_ridership": awr_value,
                "source": "Caltrain Tableau Public",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )
    return summary


def download_ridership(cfg, period_override: str | None) -> dict[str, Path]:
    cfg.ridership_monthly_dir.mkdir(parents=True, exist_ok=True)
    cfg.ridership_reference_dir.mkdir(parents=True, exist_ok=True)

    raw_payloads: dict[str, str] = {}
    for key, view in RIDERSHIP_VIEWS.items():
        raw_payloads[key] = fetch_tableau_csv(view)

    executive_period = parse_month_label(raw_payloads["executive_summary"])

    data_download = pd.read_csv(io.StringIO(raw_payloads["data_download"]))
    awr_by_fy = pd.read_csv(io.StringIO(raw_payloads["awr_by_fiscal_year"]))
    monthly_by_fy = pd.read_csv(io.StringIO(raw_payloads["monthly_ridership_by_fiscal_year"]))

    detected_period, detected_label = latest_period_from_data_download(data_download)
    period = period_override or executive_period or detected_period

    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", period):
        raise ValueError(f"Invalid period resolved: {period}")

    summary = build_latest_summary(
        period=period,
        month_label=detected_label,
        data_download=data_download,
        awr_by_fy=awr_by_fy,
    )

    outputs: dict[str, Path] = {}

    raw_dir = cfg.ridership_monthly_dir / "raw_tableau"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for key, payload in raw_payloads.items():
        path = raw_dir / f"{key}_{period}.csv"
        path.write_text(payload, encoding="utf-8")
        outputs[key] = path

    summary_out = cfg.ridership_monthly_dir / f"ridership_summary_{period}.csv"
    summary.to_csv(summary_out, index=False, quoting=csv.QUOTE_MINIMAL)
    outputs["summary"] = summary_out

    data_download_out = cfg.ridership_monthly_dir / f"ridership_data_download_{period}.csv"
    awr_out = cfg.ridership_monthly_dir / f"ridership_awr_by_fiscal_year_{period}.csv"
    monthly_out = cfg.ridership_monthly_dir / f"ridership_monthly_by_fiscal_year_{period}.csv"

    data_download.to_csv(data_download_out, index=False)
    awr_by_fy.to_csv(awr_out, index=False)
    monthly_by_fy.to_csv(monthly_out, index=False)

    outputs["data_download"] = data_download_out
    outputs["awr_by_fiscal_year"] = awr_out
    outputs["monthly_by_fiscal_year"] = monthly_out

    return outputs


def main() -> None:
    args = parse_args()
    cfg = get_agency_config(args.agency)

    if args.gtfs_only and args.ridership_only:
        raise ValueError("Choose only one of --gtfs-only or --ridership-only")

    run_gtfs = not args.ridership_only
    run_ridership = not args.gtfs_only

    if run_gtfs:
        gtfs_zip, extracted = download_gtfs(cfg, skip_extract=args.skip_gtfs_extract)
        print(f"Downloaded Caltrain GTFS zip: {gtfs_zip}")
        if args.skip_gtfs_extract:
            print("Skipped GTFS extraction (--skip-gtfs-extract)")
        else:
            print(f"Extracted GTFS text files into {cfg.gtfs_dir}: {extracted} files")

    if run_ridership:
        ridership_outputs = download_ridership(cfg, period_override=args.period)
        print("Downloaded Caltrain ridership tables:")
        for key in sorted(ridership_outputs.keys()):
            print(f"- {key}: {ridership_outputs[key]}")

        summary = pd.read_csv(ridership_outputs["summary"])
        row = summary.iloc[0]
        total = row["total_ridership"]
        awr = row["average_weekday_ridership"]
        total_text = f"{total:,.0f}" if pd.notna(total) else "n/a"
        awr_text = f"{awr:,.0f}" if pd.notna(awr) else "n/a"

        print()
        print(f"Latest period: {row['period']} ({row['month_label']})")
        print(f"Total ridership: {total_text}")
        print(f"Average weekday ridership: {awr_text}")


if __name__ == "__main__":
    main()