from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen


RIDERSHIP_PAGE_URL = "https://www.bart.gov/about/reports/ridership"
STATION_CODES_URL = "https://www.bart.gov/sites/default/files/docs/station-names.xls"
RIDERSHIP_LINK_PATTERN = re.compile(
    r"(?:https://www\.bart\.gov)?/sites/default/files/\d{4}-\d{2}/Ridership_(\d{6})\.xlsx"
)

DATA_DIR = Path("data/bart/ridership")
MONTHLY_DIR = DATA_DIR / "monthly"
REFERENCE_DIR = DATA_DIR / "reference"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_html(url: str) -> str:
    request = Request(url, headers=REQUEST_HEADERS)
    with urlopen(request) as response:
        return response.read().decode("utf-8", errors="ignore")


def latest_ridership_link(page_html: str) -> tuple[str, str]:
    links_by_period: dict[str, str] = {}

    for match in RIDERSHIP_LINK_PATTERN.finditer(page_html):
        period = match.group(1)
        links_by_period[period] = urljoin(RIDERSHIP_PAGE_URL, match.group(0))

    if not links_by_period:
        raise RuntimeError("No monthly ridership workbook links found on BART ridership page.")

    latest_period = max(links_by_period.keys())
    return latest_period, links_by_period[latest_period]


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers=REQUEST_HEADERS)

    with urlopen(request) as response:
        destination.write_bytes(response.read())


def main() -> None:
    print(f"Fetching ridership page: {RIDERSHIP_PAGE_URL}")
    ridership_page_html = fetch_html(RIDERSHIP_PAGE_URL)

    period, workbook_url = latest_ridership_link(ridership_page_html)
    workbook_path = MONTHLY_DIR / f"Ridership_{period}.xlsx"

    print(f"Latest monthly OD period discovered: {period}")
    print(f"Downloading workbook: {workbook_url}")
    download_file(workbook_url, workbook_path)

    station_codes_path = REFERENCE_DIR / "station-names.xls"
    print(f"Downloading station code reference: {STATION_CODES_URL}")
    download_file(STATION_CODES_URL, station_codes_path)

    print()
    print("Download complete")
    print(f"Workbook: {workbook_path}")
    print(f"Station codes: {station_codes_path}")


if __name__ == "__main__":
    main()
