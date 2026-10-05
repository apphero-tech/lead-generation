"""Step 2: polite, cached, best-first crawl of an institution's own public web pages.

- robots.txt is honoured per host (Disallow and Crawl-delay).
- At most one request per host every `per_host_delay` seconds.
- Every fetched page is cached in the database, so a re-run never re-downloads it.
- Pages are visited in order of how likely they are to list leaders (cabinet, directory, ...).
"""
from __future__ import annotations

import heapq
import logging
import re
import time
from typing import Dict, Optional, Set, Tuple
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import Settings
from .db import now_iso

log = logging.getLogger(__name__)

# Words in a URL or link text that suggest a page lists leaders or staff. Weight = priority.
POSITIVE = {
    "cabinet": 9, "leadership": 9, "senior-leadership": 9, "administration": 7, "org-chart": 7,
    "organizational-chart": 7, "our-team": 6, "staff": 6, "directory": 6, "team": 4, "people": 4,
    "contact": 3, "about": 2, "president": 4, "office-of-the-president": 6,
    "enrollment": 6, "admissions": 4, "admission": 4, "registrar": 6,
    "advancement": 7, "foundation": 6, "development": 3, "alumni": 5, "giving": 3,
    "philanthropy": 7, "philanthropic": 6, "donor": 5, "gift": 3, "give": 2, "fundraising": 6,
    "career-services": 5, "career-center": 5, "employer": 3, "registrar": 6,
    "information-technology": 6, "technology": 3, "cio": 6, "enterprise": 4, "crm": 5,
    "continuing": 6, "professional-education": 6, "executive-education": 6, "extended": 4,
    "workforce": 6, "professional-development": 3,
    "vice-president": 6, "vice president": 6, "director": 3, "dean": 2, "who-we-are": 4,
}
NEGATIVE = re.compile(
    r"(calendar|event|login|signon|sso|cart|catalog|course|athletic|sports|ticket|weather|webmail|"
    r"(?<!site)maps?\b|campus-map|virtual-tour|apply|application|portal|canvas|elearning|privacy|accessib|feed|wp-json|"
    r"/tag/|/category/|/author/|\?share=|replytocom|print=|/search|\.(pdf|docx?|xlsx?|pptx?|"
    r"jpe?g|png|gif|svg|webp|mp4|mp3|zip|ics)$)",
    re.I,
)
# Paginated listings: followed for directories (all pages of a staff directory), never for blogs.
PAGINATION = re.compile(r"(/page/\d+|[?&](paged?|pg)=\d+)", re.I)
DIRECTORY_LIKE = re.compile(r"(director(y|ies)|staff|people|team|faculty|employees|personnel)", re.I)
NEWS = re.compile(r"(/news/|/press|/stories/|/20\d\d/\d\d/|/blog/)", re.I)
EXTERNAL_OK = re.compile(r"(foundation|alumni|giving|advancement)", re.I)
# Third-party platforms whose pages describe the platform's own staff, not the institution's.
PLATFORM_HOST = re.compile(r"(zoom\.us|facebook|instagram|linkedin|twitter|x\.com|youtube|google|microsoft|"
                           r"blackbaud|givecampus|eventbrite|salesforce|hubspot|wix|squarespace)", re.I)
# Sub-sites of central offices: their staff/leadership pages get seeded as soon as the host is seen.
CENTRAL_HOST = re.compile(
    r"^(www\.)?(registrar|admissions?|enroll\w*|uff|foundation|giving|give|advancement|alumni|it|its|"
    r"cio|oit|pwd|continuing|ce|pce|extended|exed|executive-?education|workforce|president|online)\.",
    re.I,
)
HOST_SEED_PATHS = ["/", "/about/", "/about/staff/", "/staff/", "/leadership/", "/about/leadership/",
                   "/our-team/", "/team/", "/about-us/", "/contact/", "/directory/", "/about/our-team/"]
COMMON_PATHS = [
    "/about/leadership/", "/leadership/", "/about/administration/", "/administration/",
    "/president/cabinet/", "/president/leadership/", "/about/president/cabinet/", "/directory/",
    "/staff-directory/", "/about/leadership-team/", "/offices/",
]


def registered_domain(host: str) -> str:
    labels = host.lower().split(".")
    if labels[0] == "www":
        labels = labels[1:]
    if labels and labels[-1] == "us" and len(labels) >= 4:
        return ".".join(labels[-4:])  # e.g. district.k12.fl.us
    return ".".join(labels[-2:])


NON_PROD_HOST = re.compile(r"^(archive|test|dev|staging|stage|qa|old|legacy)[.\-]", re.I)


def campus_slugs(terms) -> list:
    """'Fort Lauderdale' -> ['fort lauderdale', 'fort-lauderdale', 'fortlauderdale', 'fort_lauderdale']."""
    out = []
    for t in terms or []:
        t = re.sub(r"[^a-z ]", " ", t.lower()).split()
        if t:
            out += [" ".join(t), "-".join(t), "".join(t), "_".join(t)]
    return list(dict.fromkeys(x for x in out if len(x) >= 4))


def mentions_campus(text: str, slugs) -> bool:
    text = text.lower()
    return any(s in text for s in slugs)


def score_link(url: str, anchor: str, slugs=()) -> int:
    text = (url + " " + anchor).lower()
    if NEGATIVE.search(url) or NON_PROD_HOST.search(urlparse(url).netloc):
        return -1
    if PAGINATION.search(url) and not DIRECTORY_LIKE.search(url):
        return -1
    score = sum(w for k, w in POSITIVE.items() if k in text)
    if slugs and mentions_campus(text, slugs):
        score += 20  # shared network website: this campus's own pages first
    if NEWS.search(url):
        score = min(score, 2)  # news can name leaders, but is a weaker and older source
    return score


class Crawler:
    def __init__(self, conn, settings: Settings, progress=None):
        self.conn = conn
        self.s = settings
        self.progress = progress
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent, "Accept": "text/html,*/*;q=0.5"})
        self.robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self.last_block_reason = ""
        self.last_hit: Dict[str, float] = {}

    # --- politeness -----------------------------------------------------------------
    def _robots(self, scheme: str, host: str) -> Optional[robotparser.RobotFileParser]:
        if host in self.robots:
            return self.robots[host]
        rp: Optional[robotparser.RobotFileParser] = robotparser.RobotFileParser()
        try:
            self._wait(host)
            resp = self.session.get(f"{scheme}://{host}/robots.txt", timeout=self.s.request_timeout)
            if resp.status_code in (401, 403):
                rp.disallow_all = True  # conservative: treat a refused robots.txt as "keep out"
                log.warning("%s refuses access to robots.txt (HTTP %s); site skipped", host, resp.status_code)
            elif resp.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(resp.text.splitlines())
        except requests.RequestException as exc:
            log.warning("robots.txt unreachable for %s (%s); host skipped", host, exc)
            rp = None
        self.robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        rp = self._robots(p.scheme, p.netloc)
        return bool(rp) and rp.can_fetch(self.s.user_agent, url)

    def _wait(self, host: str) -> None:
        delay = self.s.per_host_delay
        rp = self.robots.get(host)
        if rp:
            cd = rp.crawl_delay(self.s.user_agent)
            if cd:
                delay = max(delay, float(cd))
        elapsed = time.monotonic() - self.last_hit.get(host, 0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_hit[host] = time.monotonic()

    # --- fetching -------------------------------------------------------------------
    def fetch(self, url: str, refresh: bool = False) -> Optional[dict]:
        """Return the cached or freshly fetched page row, or None if blocked/unfetchable."""
        if not refresh:
            row = self.conn.execute("SELECT * FROM pages WHERE url = ?", (url,)).fetchone()
            if row:
                return dict(row)
        if not self.allowed(url):
            log.debug("robots.txt disallows %s", url)
            return None
        host = urlparse(url).netloc
        self._wait(host)
        status, html, ctype, error, final_url = None, None, None, None, url
        try:
            resp = self.session.get(url, timeout=self.s.request_timeout, stream=True, allow_redirects=True)
            status = resp.status_code
            ctype = resp.headers.get("Content-Type", "")
            final_url = resp.url
            if status == 200 and ("html" in ctype.lower() or "xml" in ctype.lower()):
                body = resp.raw.read(self.s.max_page_bytes, decode_content=True)
                html = body.decode(resp.encoding or "utf-8", errors="replace")
            resp.close()
        except requests.RequestException as exc:
            error = str(exc)[:300]
        row = {
            "url": url, "host": host, "status": status, "fetched_at": now_iso(),
            "content_type": ctype, "html": html, "source_date": None, "error": error,
        }
        if html:
            row["source_date"] = page_date(html, final_url)
        self.conn.execute(
            "INSERT INTO pages (url, host, status, fetched_at, content_type, html, source_date, error) "
            "VALUES (:url, :host, :status, :fetched_at, :content_type, :html, :source_date, :error) "
            "ON CONFLICT (url) DO UPDATE SET status = excluded.status, fetched_at = excluded.fetched_at, "
            "content_type = excluded.content_type, html = excluded.html, "
            "source_date = excluded.source_date, error = excluded.error",
            row,
        )
        self.conn.commit()
        if error:
            log.info("fetch failed %s: %s", url, error)
        return row

    def submit(self, action: str, method: str, params: dict) -> Optional[str]:
        """Submit a search form politely (robots.txt + per-host delay). Returns the HTML or None."""
        if not self.allowed(action):
            return None
        self._wait(urlparse(action).netloc)
        try:
            if method == "post":
                resp = self.session.post(action, data=params, timeout=self.s.request_timeout)
            else:
                resp = self.session.get(action, params=params, timeout=self.s.request_timeout)
        except requests.RequestException as exc:
            log.info("directory query failed %s: %s", action, exc)
            return None
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "").lower():
            return None
        return resp.text[: self.s.max_page_bytes]

    # --- crawl ----------------------------------------------------------------------
    def crawl(self, unitid: str, website: str, refresh: bool = False, campus_terms=None) -> int:
        start = normalize_url(website)
        if not start:
            return 0
        base = registered_domain(urlparse(start).netloc)
        slugs = campus_slugs(campus_terms)
        allowed_external: Set[str] = set()
        heap: list = []
        seen: Set[str] = set()
        counter = 0

        def push(url: str, depth: int, score: int) -> None:
            nonlocal counter
            if url in seen or score < 0 or depth > self.s.max_depth:
                return
            seen.add(url)
            counter += 1
            heapq.heappush(heap, (-score, depth, counter, url))

        push(start, 0, 100)
        root = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
        for path in COMMON_PATHS:
            push(root + path, 1, 8)
        for url in self.sitemap_urls(root, refresh):
            sc = score_link(url, "", slugs)
            if sc >= 4:
                push(url, 1, sc)

        seeded_hosts: Set[str] = {urlparse(start).netloc}
        per_host: Dict[str, int] = {}
        fetched = 0
        while heap and fetched < self.s.max_pages_per_institution:
            neg, depth, _, url = heapq.heappop(heap)
            host = urlparse(url).netloc
            section = section_of(url)
            # Spread the budget over many offices: per sub-site (UF: admissions.ufl.edu...) AND per
            # top-level section of a site (Nevada State: nevadastate.edu/admissions/, /community/...).
            if per_host.get(host, 0) >= self.s.max_pages_per_host or \
                    per_host.get(section, 0) >= self.s.max_pages_per_section:
                continue
            per_host[host] = per_host.get(host, 0) + 1
            per_host[section] = per_host.get(section, 0) + 1
            if self.progress:
                self.progress.check()
            row = self.fetch(url, refresh)
            if not row:
                continue
            fetched += 1
            if self.progress:
                self.progress.incr("pages")
            self.conn.execute(
                "INSERT OR IGNORE INTO institution_pages (unitid, url, depth) VALUES (?, ?, ?)",
                (unitid, url, depth),
            )
            if not row.get("html"):
                continue
            for link, anchor in extract_links(row["html"], url):
                host = urlparse(link).netloc.lower()
                in_scope = registered_domain(host) == base or host in allowed_external
                if (not in_scope and EXTERNAL_OK.search(host) and not PLATFORM_HOST.search(host)
                        and len(allowed_external) < 3):
                    # Foundations often live on their own domain (e.g. xyzfoundation.org).
                    allowed_external.add(host)
                    in_scope = True
                if not in_scope:
                    continue
                if host not in seeded_hosts and (CENTRAL_HOST.search(host) or "foundation" in host):
                    seeded_hosts.add(host)
                    for path in HOST_SEED_PATHS:
                        push(f"{urlparse(link).scheme}://{host}{path}", depth + 1, 12)
                sc = score_link(link, anchor, slugs)
                if sc > 0 or depth < 1:
                    push(link, depth + 1, sc)
        self.conn.commit()
        log.info("%s: %d pages available (%d queued in total)", unitid, fetched, len(seen))
        if fetched == 0:
            rp = self.robots.get(urlparse(start).netloc)
            self.last_block_reason = ("site refuses automated access (robots.txt)" if rp is None or rp.disallow_all
                                      else "website unreachable")
        return fetched

    def sitemap_urls(self, root: str, refresh: bool, limit: int = 5000) -> list:
        urls: list = []
        queue = [root + "/sitemap_index.xml", root + "/sitemap.xml"]
        visited: Set[str] = set()
        while queue and len(visited) < 15 and len(urls) < limit:
            sm = queue.pop(0)
            if sm in visited or not self.allowed(sm):
                continue
            visited.add(sm)
            host = urlparse(sm).netloc
            self._wait(host)
            try:
                resp = self.session.get(sm, timeout=self.s.request_timeout)
            except requests.RequestException:
                continue
            if resp.status_code != 200:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text)
            for loc in locs:
                if loc.endswith(".xml") or "sitemap" in loc.split("/")[-1]:
                    # Sub-sitemaps: skip only news/blog/product feeds (never score them as pages:
                    # "sitemap" itself must not trip the "map" exclusion).
                    if not re.search(r"(post|news|event|tag|category|product|attachment|author)", loc):
                        queue.append(loc)
                else:
                    urls.append(normalize_url(loc))
        return [u for u in urls if u]


def section_of(url: str) -> str:
    """'https://x.edu/admissions/tours/' -> 'x.edu/admissions' (the site's top-level section)."""
    p = urlparse(url)
    first = next((seg for seg in p.path.split("/") if seg), "")
    return f"{p.netloc}/{first.lower()}"


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    path = p.path or "/"
    query = ("?" + p.query) if p.query else ""
    return f"{p.scheme}://{p.netloc.lower()}{path}{query}"


def extract_links(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        link = normalize_url(urljoin(base_url, href))
        if link:
            yield link, a.get_text(" ", strip=True)[:120]


DATE_META = (
    ("meta", {"property": "article:modified_time"}),
    ("meta", {"property": "og:updated_time"}),
    ("meta", {"property": "article:published_time"}),
    ("meta", {"name": "last-modified"}),
    ("meta", {"name": "date"}),
)


def page_date(html: str, url: str) -> Optional[str]:
    """Best-effort date of the page content (YYYY-MM-DD), from meta tags, <time>, or the URL."""
    soup = BeautifulSoup(html[:200_000], "html.parser")
    for tag, attrs in DATE_META:
        el = soup.find(tag, attrs=attrs)
        if el and el.get("content"):
            m = re.search(r"(20\d\d|19\d\d)-(\d\d)-(\d\d)", el["content"])
            if m:
                return m.group(0)
    el = soup.find("time", attrs={"datetime": True})
    if el:
        m = re.search(r"(20\d\d|19\d\d)-(\d\d)-(\d\d)", el["datetime"])
        if m:
            return m.group(0)
    m = re.search(r"/(20\d\d)/(\d\d)/", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    return None
