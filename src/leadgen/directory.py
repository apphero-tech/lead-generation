"""Step 3b: look people up in the institution's own official directory search (generic, any site).

The directory page is found among the crawled pages (or at directory.<domain>). Its search form is
analysed automatically: either separate first/last-name fields or one free-text field. Each person
is searched by name; an email/phone is accepted only from the result block that carries that
person's name. Results are cached in the database, so re-runs do not repeat queries.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .crawler import Crawler, registered_domain
from .db import now_iso
from .email_pattern import EMAIL_RE, email_matches_name
from .extract import PHONE_RE, format_phone, page_lines
from .names import strip_accents

log = logging.getLogger(__name__)

DIRECTORY_URL = re.compile(r"(directory|people-?search|find-?(people|a-person)|phonebook|/people/?$)", re.I)
DIRECTORY_TEXT = re.compile(r"\b(directory|people search|find (people|a person|faculty)|phone ?book)\b", re.I)
FIRST_FIELD = re.compile(r"(first|fname|given|forename)", re.I)
LAST_FIELD = re.compile(r"(last|lname|surname|family|\bsn\b)", re.I)
QUERY_FIELD = re.compile(r"^(q|query|search|searchterm|term|keywords?|name|fullname|s|cn|text|search_?text)$", re.I)
SITE_SEARCH_ACTION = re.compile(r"(google|cse|search\.(php|aspx)?$|/search/?$|gsa)", re.I)
# A result line starting a new person entry: "Doe, Jane" or "Jane Doe".
LAST_FIRST = re.compile(r"^[A-Z][\w'’\-]+(?: [A-Z][\w'’\-]+)?, [A-Z][\w'’\-]+")
CREATE = """
CREATE TABLE IF NOT EXISTS directories (
    unitid      TEXT PRIMARY KEY,
    page_url    TEXT,
    form        TEXT,
    status      TEXT,
    detail      TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS directory_hits (
    unitid      TEXT NOT NULL,
    person_key  TEXT NOT NULL,
    query_url   TEXT,
    email       TEXT,
    phone       TEXT,
    entries     INTEGER,
    looked_up_at TEXT,
    PRIMARY KEY (unitid, person_key)
);
"""


@dataclass
class DirectoryForm:
    page_url: str
    action: str
    method: str
    first_field: str = ""
    last_field: str = ""
    query_field: str = ""
    fixed: Dict[str, str] = field(default_factory=dict)

    def params(self, first: str, last: str) -> Dict[str, str]:
        p = dict(self.fixed)
        if self.first_field and self.last_field:
            p[self.first_field] = first
            p[self.last_field] = last
        else:
            p[self.query_field] = f"{first} {last}"
        return p


@dataclass
class DirectoryHit:
    email: str = ""
    phone: str = ""
    entries: int = 0  # how many result entries carry this name (2+ = homonyms)
    query_url: str = ""


def analyse_form(html: str, page_url: str) -> Optional[DirectoryForm]:
    """Find a people-search form on a page and describe how to fill it."""
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        action = urljoin(page_url, form.get("action") or page_url)
        texts = [i for i in form.find_all("input") if (i.get("type") or "text").lower() in ("text", "search")]
        names = [i.get("name") or "" for i in texts if i.get("name")]
        first = next((n for n in names if FIRST_FIELD.search(n)), "")
        last = next((n for n in names if LAST_FIELD.search(n)), "")
        query = next((n for n in names if QUERY_FIELD.match(n)), "")
        if not (first and last) and not query:
            continue
        if not (first and last) and SITE_SEARCH_ACTION.search(action) and not DIRECTORY_URL.search(action):
            continue  # the site-wide search box, not a people directory
        fixed: Dict[str, str] = {}
        for inp in form.find_all("input"):
            name, typ = inp.get("name"), (inp.get("type") or "text").lower()
            if not name or name in (first, last, query):
                continue
            if typ == "hidden":
                fixed[name] = inp.get("value", "")
            elif typ in ("radio", "checkbox") and inp.has_attr("checked"):
                fixed[name] = inp.get("value", "on")
            elif typ in ("text", "search", "email"):
                fixed[name] = ""
        for sel in form.find_all("select"):
            if sel.get("name"):
                opt = sel.find("option", selected=True) or sel.find("option")
                fixed[sel["name"]] = opt.get("value", opt.get_text(strip=True)) if opt else ""
        return DirectoryForm(page_url, action, (form.get("method") or "get").lower(),
                             first, last, "" if (first and last) else query, fixed)
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", strip_accents(s).lower()).strip()


def parse_results(html: str, first: str, last: str) -> DirectoryHit:
    """Pick the email/phone from the result entries that carry this person's name."""
    lines = page_lines(html)
    f, l = _norm(first), _norm(last)
    hits: List[Tuple[str, str]] = []
    for i, line in enumerate(lines):
        n = _norm(line)
        if not n or len(line) > 80:
            continue
        words = n.replace(",", " ").split()
        if not (l in words and (f in words or any(w.startswith(f[:3]) for w in words if len(f) >= 3))):
            continue
        email, phone = "", ""
        for k in range(i, min(len(lines), i + 12)):
            if k > i and LAST_FIRST.match(lines[k]) and l not in _norm(lines[k]).split():
                break  # next person's entry
            for addr in EMAIL_RE.findall(lines[k]):
                if not email:
                    email = addr.lower()
            m = PHONE_RE.search(lines[k])
            if m and not phone and not re.search(r"\bfax\b", lines[k], re.I):
                phone = format_phone(m)
        if email or phone:
            hits.append((email, phone))
    distinct = list(dict.fromkeys(hits))
    if not distinct:
        return DirectoryHit()
    # Prefer the entry whose email looks like the name; otherwise the first entry.
    best = next((h for h in distinct if h[0] and email_matches_name(h[0], first, last)), distinct[0])
    return DirectoryHit(email=best[0], phone=best[1], entries=len({h[0] or h[1] for h in distinct}))


class Directory:
    def __init__(self, conn, crawler: Crawler):
        self.conn = conn
        self.crawler = crawler
        conn.executescript(CREATE)

    def candidate_forms(self, unitid: str, website: str) -> List[DirectoryForm]:
        """Every people-search form found, best first (central directory before unit pages)."""
        base = registered_domain(urlparse(website).netloc)
        rows = self.conn.execute(
            "SELECT p.url, p.html FROM institution_pages ip JOIN pages p ON p.url = ip.url "
            "WHERE ip.unitid = ? AND p.html IS NOT NULL", (unitid,)
        ).fetchall()
        pages = [(r["url"], r["html"]) for r in rows
                 if DIRECTORY_URL.search(r["url"]) or DIRECTORY_TEXT.search(_title(r["html"]))]
        for url in (f"https://directory.{base}/", f"https://www.{base}/directory/", f"https://{base}/directory/"):
            if url not in {u for u, _ in pages}:
                page = self.crawler.fetch(url)
                if page and page.get("html"):
                    pages.append((url, page["html"]))
        forms = []
        for url, html in pages:
            form = analyse_form(html, url)
            if form and form.action not in {f.action for f in forms}:
                forms.append(form)

        def rank(f: DirectoryForm):
            host = urlparse(f.page_url).netloc
            central = re.match(r"^(directory|phonebook|people|search)\.", host) is not None
            main_site = host in (base, "www." + base)
            return (not central, not main_site, not (f.first_field and f.last_field), len(f.page_url))
        return sorted(forms, key=rank)

    def find_form(self, unitid: str, website: str, probes: List[Tuple[str, str]]) -> Optional[DirectoryForm]:
        """Choose the directory form: the best-ranked one that returns results for known people."""
        row = self.conn.execute("SELECT * FROM directories WHERE unitid = ?", (unitid,)).fetchone()
        if row:
            return DirectoryForm(**json.loads(row["form"])) if row["status"] == "ok" else None
        forms = self.candidate_forms(unitid, website)
        chosen, tried = None, []
        for form in forms[:4]:
            tried.append(form.page_url)
            for first, last in probes[:3]:
                html = self.crawler.submit(form.action, form.method, form.params(first, last))
                hit = parse_results(html, first, last) if html else DirectoryHit()
                if hit.email or hit.phone:
                    chosen = form
                    break
            if chosen:
                break
        status = "ok" if chosen else "none"
        detail = chosen.page_url if chosen else (
            f"no form returned results (tried: {', '.join(tried)})" if tried else "no directory search form found")
        self.conn.execute(
            "INSERT OR REPLACE INTO directories (unitid, page_url, form, status, detail, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (unitid, chosen.page_url if chosen else None, json.dumps(chosen.__dict__) if chosen else None,
             status, detail, now_iso()),
        )
        self.conn.commit()
        log.info("%s: directory search %s (%s)", unitid, status, detail)
        return chosen

    def lookup(self, unitid: str, form: DirectoryForm, person_key: str, first: str, last: str) -> DirectoryHit:
        row = self.conn.execute(
            "SELECT * FROM directory_hits WHERE unitid = ? AND person_key = ?", (unitid, person_key)
        ).fetchone()
        if row:
            return DirectoryHit(row["email"] or "", row["phone"] or "", row["entries"] or 0, row["query_url"] or "")
        params = form.params(first, last)
        query_url = form.action + ("?" if "?" not in form.action else "&") + urlencode(params)
        hit = DirectoryHit(query_url=query_url)
        if self.crawler.allowed(form.action):
            html = self.crawler.submit(form.action, form.method, params)
            if html:
                hit = parse_results(html, first, last)
                hit.query_url = query_url
        self.conn.execute(
            "INSERT OR REPLACE INTO directory_hits (unitid, person_key, query_url, email, phone, entries, looked_up_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unitid, person_key, hit.query_url, hit.email, hit.phone, hit.entries, now_iso()),
        )
        self.conn.commit()
        return hit


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.I | re.S)
    return m.group(1) if m else ""
