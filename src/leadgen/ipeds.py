"""Step 1: load institutions (campus level + system level) from the free IPEDS directory file."""
from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from importlib import resources
from pathlib import Path
from typing import Dict, List

import requests

from .config import Settings

log = logging.getLogger(__name__)

IPEDS_URL = "https://nces.ed.gov/ipeds/datacenter/data/HD{year}.zip"

SECTORS = {
    "0": "Administrative unit",
    "1": "Public, 4-year or above",
    "2": "Private nonprofit, 4-year or above",
    "3": "Private for-profit, 4-year or above",
    "4": "Public, 2-year",
    "5": "Private nonprofit, 2-year",
    "6": "Private for-profit, 2-year",
    "7": "Public, less-than 2-year",
    "8": "Private nonprofit, less-than 2-year",
    "9": "Private for-profit, less-than 2-year",
    "99": "Sector unknown",
}


def download_hd(settings: Settings) -> Path:
    settings.ipeds_dir.mkdir(parents=True, exist_ok=True)
    target = settings.ipeds_dir / f"hd{settings.ipeds_year}.csv"
    if target.exists():
        return target
    url = IPEDS_URL.format(year=settings.ipeds_year)
    log.info("Downloading IPEDS directory file %s", url)
    resp = requests.get(url, timeout=120, headers={"User-Agent": settings.user_agent})
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        target.write_bytes(zf.read(name))
    return target


def normalize_website(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or raw in ("-1", "-2"):
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def load_system_websites() -> Dict[str, str]:
    text = resources.files("leadgen").joinpath("systems.json").read_text(encoding="utf-8")
    return {k: v for k, v in json.loads(text).items() if not k.startswith("_")}


def read_state(csv_path: Path, state: str) -> List[dict]:
    with open(csv_path, encoding="latin-1", newline="") as fh:
        reader = csv.DictReader(fh)
        # The first header carries a UTF-8 BOM read as latin-1; normalise it.
        reader.fieldnames = [f.lstrip("﻿").replace("ï»¿", "") for f in reader.fieldnames]
        rows = [r for r in reader if r["STABBR"].strip() == state]
    institutions = []
    for r in rows:
        if r.get("CYACTIVE", "1").strip() not in ("1", ""):
            continue  # closed / not active in the current year
        system = r.get("F1SYSNAM", "").strip()
        institutions.append({
            "unitid": r["UNITID"].strip(),
            "state": state,
            "name": r["INSTNM"].strip(),
            "website": normalize_website(r["WEBADDR"]),
            "city": r.get("CITY", "").strip(),
            "sector": SECTORS.get(r["SECTOR"].strip(), r["SECTOR"].strip()),
            "system_name": "" if system in ("-1", "-2") else system,
            "is_system": 0,
        })
    return institutions


def system_entities(institutions: List[dict], state: str) -> List[dict]:
    websites = load_system_websites()
    names = sorted({i["system_name"] for i in institutions if i["system_name"]})
    out = []
    for name in names:
        out.append({
            "unitid": "SYS-" + state + "-" + "".join(c for c in name.upper() if c.isalnum())[:40],
            "state": state,
            "name": name,
            "website": websites.get(name, ""),
            "city": "",
            "sector": "System office",
            "system_name": name,
            "is_system": 1,
        })
    return out


def load_institutions(conn, settings: Settings, state: str) -> int:
    csv_path = download_hd(settings)
    campuses = read_state(csv_path, state)
    systems = system_entities(campuses, state)
    for inst in campuses + systems:
        conn.execute(
            "INSERT INTO institutions (unitid, state, name, website, city, sector, system_name, is_system) "
            "VALUES (:unitid, :state, :name, :website, :city, :sector, :system_name, :is_system) "
            "ON CONFLICT (unitid) DO UPDATE SET name = excluded.name, website = excluded.website, "
            "city = excluded.city, "
            "sector = excluded.sector, system_name = excluded.system_name",
            inst,
        )
    conn.commit()
    log.info("%s: %d campuses and %d system offices loaded", state, len(campuses), len(systems))
    return len(campuses) + len(systems)
