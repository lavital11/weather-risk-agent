"""Paths and the hub registry (config/hubs.toml)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DB_PATH = ROOT / "data" / "weather_risk.db"


@dataclass(frozen=True)  # frozen = read-only after creation
class Hub:
    id: str
    name: str
    airport: str
    lat: float
    lon: float
    timezone: str
    state: str
    state_fips: str
    county_fips: str
    county: str
    region: str


def load_hubs(path: Path = CONFIG_DIR / "hubs.toml") -> list[Hub]:
    with open(path, "rb") as f:  # tomllib requires binary mode
        raw = tomllib.load(f)["hubs"]
    # Hub(**h) raises TypeError if a field is missing or misspelled,
    # so a broken config fails at startup, not halfway through a run.
    hubs = [Hub(**h) for h in raw]
    ids = [h.id for h in hubs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate hub id in {path}")
    return hubs
