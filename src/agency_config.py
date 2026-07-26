from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgencyConfig:
    agency_id: str
    display_name: str
    gtfs_dir: Path
    output_dir: Path
    ridership_output_dir: Path
    ridership_monthly_dir: Path
    ridership_reference_dir: Path
    line_color: str
    od_name_aliases: dict[str, str]


AGENCY_CONFIGS: dict[str, AgencyConfig] = {
    "bart": AgencyConfig(
        agency_id="bart",
        display_name="BART",
        gtfs_dir=Path("data/bart/gtfs/current"),
        output_dir=Path("output/bart"),
        ridership_output_dir=Path("output/bart/ridership"),
        ridership_monthly_dir=Path("data/bart/ridership/monthly"),
        ridership_reference_dir=Path("data/bart/ridership/reference"),
        line_color="#0a82ca",
        od_name_aliases={
            "berkeley": "downtownberkeley",
            "civiccenter": "civiccenterunplaza",
            "millbrae": "millbraecaltraintransferplatform",
            "northconcord": "northconcordmartinez",
            "oaklandinternationalairport": "oaklandinternationalairportstation",
            "pleasanthill": "pleasanthillcontracostacentre",
            "warmsprings": "warmspringssouthfremont",
        },
    ),
}


_PERIOD_FILE_PATTERN = re.compile(r"(.+?)_(\d{6})\.csv$")


def get_agency_config(agency_id: str) -> AgencyConfig:
    key = str(agency_id).strip().lower()
    if key not in AGENCY_CONFIGS:
        available = ", ".join(sorted(AGENCY_CONFIGS.keys()))
        raise ValueError(f"Unknown agency '{agency_id}'. Available: {available}")
    return AGENCY_CONFIGS[key]


def latest_period_csv(directory: Path, prefix: str) -> tuple[Path, str]:
    candidates: list[tuple[str, Path]] = []
    for path in directory.glob(f"{prefix}_*.csv"):
        match = _PERIOD_FILE_PATTERN.match(path.name)
        if match and match.group(1) == prefix:
            candidates.append((match.group(2), path))

    if not candidates:
        raise FileNotFoundError(f"No files found for prefix '{prefix}' in {directory}")

    period, file_path = max(candidates, key=lambda item: item[0])
    return file_path, period
