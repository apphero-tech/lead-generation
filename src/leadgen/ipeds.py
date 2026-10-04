"""Step 1: load institutions (campus level + system level) from the free IPEDS directory file."""
from __future__ import annotations

import csv
import io
import difflib
import json
import math
import re
import logging
import zipfile
from importlib import resources
from pathlib import Path
from typing import Dict, List, Tuple

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


DIRECTIONS = {"n": "north", "s": "south", "e": "east", "w": "west"}
MERGE_MAX_KM = 15.0


def city_tokens(name: str) -> List[str]:
    x = re.sub(r"[^a-z ]", " ", strip_accents_basic(name or "").lower().replace(".", " "))
    toks = x.split()
    out = []
    for i, t in enumerate(toks):
        if t == "ft":
            t = "fort"
        elif t == "st":
            t = "saint"
        elif t == "mt":
            t = "mount"
        elif i == 0 and t in DIRECTIONS and len(toks) > 1:
            t = DIRECTIONS[t]
        out.append(t)
    return out


def city_key(name: str) -> str:
    """Comparison key for a city: case/punctuation/accent-free, common abbreviations expanded."""
    return "".join(city_tokens(name))


def strip_accents_basic(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def canonical_cities(entries) -> Dict[str, str]:
    """Map every raw IPEDS city spelling to one display name.

    entries: city names, or (city, lat, lon[, institution name]) tuples, for one state.
    - Spellings differing only by case, punctuation, accents or abbreviations ("Ft", "St.", "N")
      always merge.
    - Look-alike spellings ("Ft Laurderdale", "Whitter", "Washing") merge only when their
      institutions are within MERGE_MAX_KM AND the variant is evidently a typo: either the
      institution's own name carries the other spelling ("ATI College-Whittier" filed under
      "Whitter"), or the variant is used by a single institution and appears nowhere else.
    Distinct places with similar names (Santa Clara / Santa Clarita, Aguada / Aguadilla) and
    suburbs ("West Hartford") therefore stay separate.
    """
    counts: Dict[str, int] = {}
    points: Dict[str, List[Tuple[float, float]]] = {}
    owners: Dict[str, List[str]] = {}
    corpus: List[str] = []
    for e in entries:
        name, lat, lon, inst = (e, None, None, "") if isinstance(e, str) else (tuple(e) + ("",))[:4]
        n = re.sub(r"\s+", " ", name or "").strip()
        if not n:
            continue
        counts[n] = counts.get(n, 0) + 1
        owners.setdefault(n, []).append((inst or "").lower())
        if inst:
            corpus.append(inst.lower())
        try:
            points.setdefault(n, []).append((float(lat), float(lon)))
        except (TypeError, ValueError):
            points.setdefault(n, [])

    def in_text(spelling: str, texts: List[str]) -> bool:
        rx = re.compile(r"\b" + re.escape(spelling.lower()) + r"\b")
        return any(rx.search(t) for t in texts)

    def attested(spelling: str) -> bool:
        return counts[spelling] >= 2 or in_text(spelling, corpus)

    ordered = sorted(counts, key=lambda n: (-counts[n], n))
    groups: List[dict] = []
    for n in ordered:
        k, toks = city_key(n), set(city_tokens(n))
        target = next((g for g in groups if g["key"] == k), None)
        if target is None:
            for g in groups:
                if g["key"][:1] != k[:1] or toks < g["tokens"] or g["tokens"] < toks:
                    continue  # "Hartford" vs "West Hartford": a different town
                if difflib.SequenceMatcher(None, g["key"], k).ratio() < 0.8:
                    continue
                if not any(_km(a, b) <= MERGE_MAX_KM for a in points[n] for b in g["points"]):
                    continue
                # Either side may be the typo (spellings are visited by frequency, then alphabetically).
                named_after = (any(in_text(sp, owners[n]) for sp in g["spellings"])
                               or any(in_text(n, owners[sp]) for sp in g["spellings"]))
                g_attested = any(attested(sp) for sp in g["spellings"])
                lone_typo = (not attested(n) and g_attested) or (attested(n) and not g_attested)
                if named_after or lone_typo:
                    target = g
                    break
        if target is None:
            target = {"key": k, "tokens": toks, "points": [], "spellings": []}
            groups.append(target)
        target["points"].extend(points[n])
        target["spellings"].append(n)

    mapping: Dict[str, str] = {}
    for g in groups:
        # Display: most used spelling; on a tie, one attested in institution names, full words
        # over abbreviations, mixed case over ALL CAPS.
        best = max(g["spellings"], key=lambda n: (
            counts[n], in_text(n, corpus), not re.search(r"\b(ft|st|mt|n|s|e|w)\b\.?", n, re.I),
            not (n.isupper() or n.islower())))
        display = best.title() if (best.isupper() or best.islower()) else best
        for n in g["spellings"]:
            mapping[n] = display
    return mapping


def format_us_phone(raw: str) -> str:
    """IPEDS GENTELE: digits, sometimes with an extension appended (e.g. 85098357003632)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 10:
        return ""
    main, ext = digits[:10], digits[10:]
    out = f"({main[:3]}) {main[3:6]}-{main[6:]}"
    return out + (f" ext. {ext}" if ext else "")


def load_system_websites() -> Dict[str, str]:
    text = resources.files("leadgen").joinpath("systems.json").read_text(encoding="utf-8")
    return {k: v for k, v in json.loads(text).items() if not k.startswith("_")}


def read_state(csv_path: Path, state: str) -> List[dict]:
    # The IPEDS file is UTF-8 with a BOM (accents in Puerto Rico names, e.g. "Bayamón").
    with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["STABBR"].strip() == state]
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
            "city_raw": r.get("CITY", "").strip(),
            "latitude": r.get("LATITUDE", "").strip(),
            "longitude": r.get("LONGITUD", "").strip(),
            "chief_name": r.get("CHFNM", "").strip(),
            "chief_title": r.get("CHFTITLE", "").strip(),
            "main_phone": format_us_phone(r.get("GENTELE", "")),
            "sector": SECTORS.get(r["SECTOR"].strip(), r["SECTOR"].strip()),
            "system_name": "" if system in ("-1", "-2") else system,
            "is_system": 0,
        })
    mapping = canonical_cities([(i["city"], i["latitude"], i["longitude"], i["name"]) for i in institutions])
    for i in institutions:
        i["city"] = mapping.get(re.sub(r"\s+", " ", i["city"]).strip(), i["city"])
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
            "city": "", "city_raw": "", "latitude": "", "longitude": "",
            "chief_name": "", "chief_title": "", "main_phone": "",
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
            "INSERT INTO institutions (unitid, state, name, website, city, city_raw, latitude, longitude, "
            "chief_name, chief_title, main_phone, sector, system_name, is_system) VALUES (:unitid, :state, "
            ":name, :website, :city, :city_raw, :latitude, :longitude, :chief_name, :chief_title, :main_phone, "
            ":sector, :system_name, :is_system) "
            "ON CONFLICT (unitid) DO UPDATE SET name = excluded.name, website = excluded.website, "
            "city = excluded.city, city_raw = excluded.city_raw, latitude = excluded.latitude, "
            "longitude = excluded.longitude, chief_name = excluded.chief_name, chief_title = excluded.chief_title, "
            "main_phone = excluded.main_phone, "
            "sector = excluded.sector, system_name = excluded.system_name",
            inst,
        )
    conn.commit()
    log.info("%s: %d campuses and %d system offices loaded", state, len(campuses), len(systems))
    return len(campuses) + len(systems)
