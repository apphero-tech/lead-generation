"""Public salary records (e.g. TransparentNevada): names + exact job titles of a public employer's
whole staff. Used as an extra source of target people the institution's website does not name.

Only the per-employer listing pages are read (robots.txt forbids the site's search pages); records
can be several years old, so every person found here is flagged for a "still in post?" check.
"""
from __future__ import annotations

import json
import logging
import re
from importlib import resources
from typing import Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

from .crawler import Crawler
from .extract import Candidate
from .names import name_key, parse_name
from .profiles import match_title

log = logging.getLogger(__name__)

GENERIC = {"of", "the", "at", "and", "a", "inst"}


def load_sources() -> Dict[str, dict]:
    text = resources.files("leadgen").joinpath("salary_sources.json").read_text(encoding="utf-8")
    return {k: v for k, v in json.loads(text).items() if not k.startswith("_")}


def _words(name: str) -> List[str]:
    """Comparable words of an employer / institution name ('University' and 'College' are treated
    alike: Nevada State College became Nevada State University)."""
    x = re.sub(r"[^a-z ]", " ", name.lower().replace("-", " "))
    return [("inst" if w in ("university", "college", "institute") else w) for w in x.split()]


def match_employer(institution: str, slugs: Dict[str, List[int]]) -> Optional[str]:
    """The employer slug whose words equal the institution's (ignoring of/the and
    university/college), e.g. 'University of Nevada-Las Vegas' -> 'university-nevada-las-vegas'."""
    want = [w for w in _words(institution) if w not in GENERIC]
    if len(want) < 1:
        return None
    for slug in slugs:
        if [w for w in _words(slug) if w not in GENERIC] == want and "inst" in _words(slug):
            return slug
    return None


class SalaryRecords:
    def __init__(self, crawler: Crawler, max_pages: int = 1200):
        self.crawler = crawler
        self.max_pages = max_pages
        self.sources = load_sources()
        self._employers: Dict[str, Dict[str, List[int]]] = {}
        self._lists: Dict[str, List[Tuple[str, str, str]]] = {}

    def employers(self, state: str) -> Dict[str, List[int]]:
        """employer slug -> available years (from the site's salaries sitemap, cached)."""
        if state in self._employers:
            return self._employers[state]
        out: Dict[str, List[int]] = {}
        src = self.sources.get(state)
        if src:
            row = self.crawler.fetch(f"{src['site']}/salaries/sitemap.xml")
            text = (row or {}).get("html") or ""
            for year, slug in re.findall(r"<loc>[^<]*/salaries/(\d{4})/([a-z0-9-]+)</loc>", text):
                out.setdefault(slug, []).append(int(year))
        self._employers[state] = out
        return out

    def listing(self, site: str, year: int, slug: str) -> List[Tuple[str, str, str]]:
        """Every (name, title, person URL) of an employer's list for a year (pages cached)."""
        key = f"{site}|{year}|{slug}"
        if key in self._lists:
            return self._lists[key]
        base = f"{site}/salaries/{year}/{slug}"
        rows: List[Tuple[str, str, str]] = []
        for page in range(1, self.max_pages + 1):
            row = self.crawler.fetch(base if page == 1 else f"{base}?page={page}")
            if not row or not row.get("html"):
                break
            got = parse_listing(row["html"], site)
            if not got:
                break
            rows += got
            if f"page={page + 1}" not in row["html"]:
                break
        self._lists[key] = rows
        return rows

    def candidates(self, inst: dict, website_names: Set[str] = frozenset()) -> Tuple[List[Candidate], Dict[str, int]]:
        """Target people of this institution from public salary records.
        Returns (candidates, {source URL: record year})."""
        src = self.sources.get(inst.get("state", ""))
        if not src or inst.get("is_system"):
            return [], {}
        site, systems = src["site"], src.get("system_employers", [])
        slugs = self.employers(inst["state"])
        campus = match_employer(inst["name"], {s: y for s, y in slugs.items() if s not in systems})
        if not campus:
            return [], {}
        campus_year = max(slugs[campus])
        campus_rows = self.listing(site, campus_year, campus)
        campus_names = {name_key(*n) for n in (parse_name(r[0]) for r in campus_rows) if n}
        known = campus_names | set(website_names)
        out: List[Candidate] = []
        years: Dict[str, int] = {}
        recent = [(max(slugs[s]), s) for s in systems if s in slugs and max(slugs[s]) > campus_year]
        if recent:
            # A newer system-wide list exists: take current titles from it, but only for people
            # known at this campus (its own older list or its website), never other campuses' staff.
            sys_year, sys_slug = max(recent)
            for name, title, url in self.listing(site, sys_year, sys_slug):
                parsed = parse_name(name)
                if not parsed or name_key(*parsed) not in known:
                    continue
                for profile, quality in match_title(title):
                    link = url or f"{site}/salaries/{sys_year}/{sys_slug}"
                    out.append(Candidate(parsed[0], parsed[1], profile.id, quality, title, link))
                    years[link] = sys_year
        else:
            for name, title, url in campus_rows:
                parsed = parse_name(name)
                if not parsed:
                    continue
                for profile, quality in match_title(title):
                    link = url or f"{site}/salaries/{campus_year}/{campus}"
                    out.append(Candidate(parsed[0], parsed[1], profile.id, quality, title, link))
                    years[link] = campus_year
        log.info("%s: %d target people from salary records (%s, campus list %s %s%s)", inst["name"], len(out),
                 site, campus, campus_year, f", system list {recent and max(recent)[1]} {recent and max(recent)[0]}" if recent else "")
        return out, years


def parse_listing(html: str, site: str) -> List[Tuple[str, str, str]]:
    """(name, job title, person page URL) for each row of a salary listing table."""
    table = BeautifulSoup(html, "html.parser").find("table")
    if table is None:
        return []
    heads = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
    try:
        i_name, i_title = heads.index("name"), heads.index("job title")
    except ValueError:
        return []
    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(i_name, i_title):
            continue
        a = tds[i_name].find("a", href=True)
        link = (site + a["href"]) if a and a["href"].startswith("/") else (a["href"] if a else "")
        out.append((tds[i_name].get_text(" ", strip=True), tds[i_title].get_text(" ", strip=True), link))
    return out
