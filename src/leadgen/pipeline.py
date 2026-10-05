"""Orchestration: per-institution steps (crawl -> extract/merge -> emails), resumable and idempotent."""
from __future__ import annotations

import html as html_lib
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from .config import Settings
from .crawler import NEWS, Crawler, campus_slugs, mentions_campus, registered_domain
from .db import get_step, set_step
from .directory import Directory, DirectoryForm, DirectoryHit
from .email_pattern import deduce, detect_pattern, name_in_email
from .extract import (Candidate, find_candidates, name_email_pairs, page_lines, page_name_email_pairs,
                      parse_profile, profile_links)
from .names import name_key, parse_name
from .profiles import PROFILE_BY_ID, match_staff, match_title, staff_rank

log = logging.getLogger(__name__)

QUALITY_RANK = {"exact": 0, "close": 1, "fallback": 2}
IPEDS_SOURCE = "https://nces.ed.gov/collegenavigator/?id={unitid}"


# Junior "fallback" titles are dropped at institutions where this many people match properly.
FALLBACK_MAX_PEOPLE = 10


def merge_candidates(cands: List[Candidate], max_per_profile: int) -> List[dict]:
    """De-duplicate by PERSON (not by title): one record per person, holding all their profiles.

    For each profile only the best `max_per_profile` people are kept (exact title first, then the
    person seen on the most pages, then non-news sources).
    """
    people: Dict[str, dict] = {}
    for c in cands:
        key = name_key(c.first_name, c.last_name)
        p = people.setdefault(key, {
            "person_key": key, "first_name": c.first_name, "last_name": c.last_name,
            "emails": [], "phones": [], "duties": [], "roles": {},
        })
        if c.email:
            p["emails"].append((c.email, c.source_url))
        if c.phone:
            p["phones"].append((c.phone, c.source_url))
        if c.duty_sentence:
            p["duties"].append(c.duty_sentence)
        role = p["roles"].setdefault(c.profile_id, {"quality": c.match_quality, "titles": [], "sources": []})
        if QUALITY_RANK[c.match_quality] < QUALITY_RANK[role["quality"]]:
            role["quality"] = c.match_quality
        role["titles"].append(c.found_title)
        if c.source_url not in role["sources"]:
            role["sources"].append(c.source_url)

    # Rank people per profile and drop the weakest.
    by_profile: Dict[str, List[str]] = defaultdict(list)
    for key, p in people.items():
        for pid in p["roles"]:
            by_profile[pid].append(key)
    # Junior "fallback" titles only count when nobody closer exists for that profile, and only at
    # small institutions (a big university always has someone better for the job).
    proper = sum(1 for p in people.values() if any(r["quality"] != "fallback" for r in p["roles"].values()))
    for pid, keys in by_profile.items():
        if proper >= FALLBACK_MAX_PEOPLE or any(people[k]["roles"][pid]["quality"] != "fallback" for k in keys):
            for k in [k for k in keys if people[k]["roles"][pid]["quality"] == "fallback"]:
                del people[k]["roles"][pid]
                keys.remove(k)
    for pid, keys in by_profile.items():
        def rank(k: str):
            r = people[k]["roles"][pid]
            news_only = all(NEWS.search(u) for u in r["sources"])
            return (QUALITY_RANK[r["quality"]], news_only, -len(r["sources"]))
        keys.sort(key=rank)
        if max_per_profile <= 0:
            continue  # keep everyone (e.g. one Director of Development per college)
        for k in keys[max_per_profile:]:
            del people[k]["roles"][pid]
        for k in keys[:max_per_profile]:
            people[k]["roles"][pid]["competitors"] = min(len(keys), max_per_profile) - 1
    return [p for p in people.values() if p["roles"]]


UNIT_WORDS = re.compile(
    r"\b(college|school|department|dept|foundation|office|center|centre|institute|division|libraries|"
    r"health|online|program|alumni|advancement|admissions|registrar|enrollment|information technology|"
    r"continuing|workforce|graduate|undergraduate)\b", re.I)


def page_subject(page_title: str) -> str:
    """'Career Services Center - Nevada State University' -> 'Career Services Center'."""
    first = re.split(r"\s[|–—:\-]\s", page_title or "")[0].strip()
    return first if 3 <= len(first) <= 60 and not re.search(r"\b(home|welcome)\b", first, re.I) else ""


def unit_from_title(page_title: str, host: str) -> str:
    """Faculty / office the source page belongs to, from its <title> (fallback: the web host)."""
    parts = [p.strip() for p in re.split(r"\s[|–—:\-]\s|\s»\s", page_title or "") if p.strip()]
    units = [p for p in parts if UNIT_WORDS.search(p) and len(p) <= 90]
    # The most specific part is usually the one nearest the site name (the last unit-like part).
    return units[-1] if units else host


HEAD_TITLE = re.compile(r"\b(president|chancellor)\b", re.I)


def filter_heads(cands: List[Candidate], website: str) -> List[Candidate]:
    """Bare titles like "Director" identify the head only at small institutions. When many people
    match, keep only a President/Chancellor shown on the main site or the president's office site."""
    # News stories name club / student-government "presidents": never a source for the head.
    cands = [c for c in cands if c.profile_id != "head" or not NEWS.search(c.source_url)]
    # A real President/Chancellor outranks bare "Director" titles (often a department's director).
    if any(c.profile_id == "head" and HEAD_TITLE.search(c.found_title) for c in cands):
        cands = [c for c in cands if c.profile_id != "head" or HEAD_TITLE.search(c.found_title)]
    heads = {name_key(c.first_name, c.last_name) for c in cands if c.profile_id == "head"}
    if len(heads) <= 3:
        return cands
    base = registered_domain(urlparse(website).netloc)
    def official(c: Candidate) -> bool:
        host = urlparse(c.source_url).netloc.lower()
        return bool(HEAD_TITLE.search(c.found_title)) and (
            host in (base, "www." + base) or host.startswith("president."))
    return [c for c in cands if c.profile_id != "head" or official(c)]


def broad_staff(pages, limit: int) -> List[Candidate]:
    """Last-resort pass: any named staff member with a job title, ranked by seniority."""
    found: Dict[str, Candidate] = {}
    for url, html in pages:
        for c in find_candidates(page_lines(html), url, matcher=match_staff):
            key = name_key(c.first_name, c.last_name)
            if key not in found or (staff_rank(c.found_title) or 9) < (staff_rank(found[key].found_title) or 9):
                found[key] = c
    ranked = sorted(found.values(), key=lambda c: (staff_rank(c.found_title), not c.email, c.last_name))
    return ranked[:limit]


def shared_site(inst: dict) -> int:
    """Number of campuses (nationwide) whose IPEDS website is this exact site; 0 if not shared."""
    try:
        n = int(inst.get("site_shared_by") or 1)
    except ValueError:
        n = 1
    return n if n > 1 else 0


def campus_terms(inst: dict) -> List[str]:
    """Words identifying this campus on a network website: the name suffix and the city."""
    terms = []
    parts = re.split(r"\s+[-–]\s+|-(?=[A-Z])", inst.get("name") or "")
    if len(parts) > 1:
        terms.append(parts[-1])
    for c in (inst.get("city"), inst.get("city_raw")):
        if c:
            terms.append(c)
    return list(dict.fromkeys(terms))


def ipeds_chief(inst: dict, cands: List[Candidate], htmls: List[str]) -> List[Candidate]:
    """The institution's chief executive from IPEDS, so every institution has at least its head.

    Skipped when the website already shows a head (IPEDS can lag a year behind)."""
    name = parse_name(inst.get("chief_name") or "")
    if not name:
        return []
    first, last = name
    title = inst.get("chief_title") or "Chief executive"
    if any(c.profile_id == "head" for c in cands):
        return []
    # IPEDS sometimes lists whoever filed the survey ("Chief Data Officer"): only a head-like or
    # target title is labelled as such; anything else is listed as other staff.
    matches = match_title(title) or ([(PROFILE_BY_ID["head"], "exact")] if staff_rank(title) is None or
                                     re.search(r"\b(president|chancellor|director|ceo|superintendent)\b", title, re.I)
                                     else [(PROFILE_BY_ID["other"], "fallback")])
    src = IPEDS_SOURCE.format(unitid=inst["unitid"])
    return [Candidate(first, last, p.id, q, title, src) for p, q in matches]


def contact_level(inst: dict, sources: List[str], titles: Dict[str, str]) -> str:
    """'this campus' or 'network-wide' (contact found only on a website shared by many campuses)."""
    n = shared_site(inst)
    if not n:
        return "this campus"
    slugs = campus_slugs(campus_terms(inst))
    if any(u.startswith("https://nces.ed.gov/") for u in sources):
        try:
            chief_n = int(inst.get("chief_shared_by") or 1)
        except ValueError:
            chief_n = 1
        if chief_n <= 1:
            return "this campus"  # IPEDS names a chief specific to this campus
    if any(mentions_campus(u + " " + titles.get(u, ""), slugs) for u in sources):
        return "this campus"
    return f"network-wide ({n} campuses share this website)"


def pick_title(titles: List[str]) -> str:
    """Most specific title text seen for the person (longest reasonable one)."""
    clean = [t for t in titles if t and len(t) <= 160]
    return max(clean, key=len) if clean else ""


class Pipeline:
    def __init__(self, conn, settings: Settings, progress=None):
        self.conn = conn
        self.s = settings
        self.progress = progress
        self.crawler = Crawler(conn, settings, progress)
        self.directory = Directory(conn, self.crawler)

    def run_institution(self, inst: dict, refresh: bool = False) -> None:
        unitid = inst["unitid"]
        if not inst["website"]:
            set_step(self.conn, unitid, "crawl", "skipped", "no website in IPEDS / systems.json")
            set_step(self.conn, unitid, "extract", "skipped", "no website")
            return
        if refresh or get_step(self.conn, unitid, "crawl") != "done":
            set_step(self.conn, unitid, "crawl", "running")
            if self.progress:
                self.progress.update(stage="Lecture du site web")
            n = self.crawler.crawl(unitid, inst["website"], refresh=refresh,
                                   campus_terms=campus_terms(inst) if shared_site(inst) else None)
            set_step(self.conn, unitid, "crawl", "done",
                     f"{n} pages" if n else f"0 pages: {self.crawler.last_block_reason}")
        elif self.progress:
            # Already read on a previous run: count its cached pages so the counter stays meaningful.
            cached = self.conn.execute(
                "SELECT COUNT(*) FROM institution_pages WHERE unitid = ?", (unitid,)).fetchone()[0]
            self.progress.incr("pages", cached)
        set_step(self.conn, unitid, "extract", "running")
        if self.progress:
            self.progress.update(stage="Recherche des personnes et des emails")
        n = self.extract_institution(inst)
        set_step(self.conn, unitid, "extract", "done", f"{n} people")

    def extract_institution(self, inst: dict) -> int:
        unitid = inst["unitid"]
        rows = self.conn.execute(
            "SELECT p.url, p.html, p.source_date, p.fetched_at FROM institution_pages ip "
            "JOIN pages p ON p.url = ip.url WHERE ip.unitid = ? AND p.html IS NOT NULL",
            (unitid,),
        ).fetchall()
        page_info = {r["url"]: (r["source_date"], r["fetched_at"]) for r in rows}
        self._titles = {r["url"]: _page_title(r["html"]) for r in rows}
        self._main_phone = {unitid: inst.get("main_phone") or ""}
        self._inst = inst
        self._host_patterns: Dict[tuple, tuple] = {}
        cands: List[Candidate] = []
        pairs_by_domain: Dict[str, list] = defaultdict(list)
        pairs_by_host: Dict[tuple, list] = defaultdict(list)  # (web host, mail domain) -> pairs
        domains_by_host: Dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            lines = page_lines(r["html"])
            cands.extend(find_candidates(lines, r["url"], context=page_subject(self._titles.get(r["url"], ""))))
            host = urlparse(r["url"]).netloc
            pairs = list(name_email_pairs(r["html"])) + list(page_name_email_pairs(lines))
            for first, last, email in {p[2]: p for p in pairs}.values():
                domain = email.split("@")[1]
                pairs_by_domain[domain].append((first, last, email))
                pairs_by_host[(host, domain)].append((first, last, email))
                domains_by_host[host][domain] += 1

        base = registered_domain(urlparse(inst["website"]).netloc)
        cands = filter_heads(cands, inst["website"])
        if not cands:
            # Nobody for any target profile: list the staff the website does name (most senior first).
            cands = broad_staff([(r["url"], r["html"]) for r in rows], self.s.broad_max_people)
        cands.extend(ipeds_chief(inst, cands, [r["html"] for r in rows]))
        people = merge_candidates(cands, self.s.max_candidates_per_profile)

        # (a) People without an email: open their own profile page when their name is a link.
        if self.progress:
            self.progress.update(stage="Lecture des fiches individuelles")
        html_by_url = {r["url"]: r["html"] for r in rows}
        self._profile_pages(people, html_by_url, base, page_info)

        # (b) Official directory search (also confirms the person still works there).
        form = None
        hits: Dict[str, DirectoryHit] = {}
        if self.s.use_directory_search and people:
            probes = [(p["first_name"], p["last_name"]) for p in people]
            form = self.directory.find_form(unitid, inst["website"], probes)
        if form:
            if self.progress:
                self.progress.update(stage="Vérification dans l’annuaire officiel")
            for p in people:
                if self.progress:
                    self.progress.check()
                    self.progress.incr("lookups")
                hits[p["person_key"]] = self.directory.lookup(unitid, form, p["person_key"], p["first_name"], p["last_name"])

        # (c) Learn each mail domain's format, including from the addresses of the contacts found
        # above (e.g. 16 published "flast@nova.edu" addresses let us guess the other 14).
        for p in people:
            found = [e for e, _ in p["emails"]]
            hit = hits.get(p["person_key"])
            if hit and hit.email:
                found.append(hit.email)
            hosts = {urlparse(u).netloc for r in p["roles"].values() for u in r["sources"]}
            for e in dict.fromkeys(found):
                d = e.split("@")[1]
                pairs_by_domain[d].append((p["first_name"], p["last_name"], e))
                for h in hosts:
                    domains_by_host[h][d] += 1
                    pairs_by_host[(h, d)].append((p["first_name"], p["last_name"], e))
        patterns: Dict[str, tuple] = {}
        for domain, pairs in pairs_by_domain.items():
            res = detect_pattern(pairs, self.s.pattern_min_examples, self.s.pattern_min_share)
            self.conn.execute(
                "INSERT INTO email_patterns (domain, pattern, support, total, examples) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (domain) DO UPDATE SET pattern = excluded.pattern, support = excluded.support, "
                "total = excluded.total, examples = excluded.examples",
                (domain, res[0] if res else None, res[1] if res else 0, len(pairs),
                 ", ".join(res[3]) if res else ""),
            )
            if res:
                patterns[domain] = res
        # The same domain can use different formats on different sub-sites (Nova: first.last on
        # undergrad.nova.edu, flast on giving.nova.edu): learn per sub-site too, used first.
        host_patterns: Dict[tuple, tuple] = {}
        for key, pairs in pairs_by_host.items():
            res = detect_pattern(pairs, self.s.pattern_min_examples, self.s.pattern_min_share)
            if res:
                host_patterns[key] = res
        self._host_patterns = host_patterns
        # Contacts' own mail domains, most used first (some schools mail from another domain,
        # e.g. a technical college using its school district's addresses).
        contact_domains = Counter(e.split("@")[1] for p in people for e, _ in p["emails"])

        # Mail domain to use for a person found on a given web host: the domain most published on
        # that host's own pages (e.g. a college sub-site), else the institution's root domain, else
        # the domain most used by the institution's other contacts.
        def domain_for(host: str) -> str:
            local = [d for d, _ in domains_by_host.get(host, Counter()).most_common()
                     if registered_domain(d) == base]
            if local:
                return local[0]
            if base in patterns or not contact_domains:
                return base
            return contact_domains.most_common(1)[0][0]

        self.conn.execute(
            "DELETE FROM person_roles WHERE person_id IN (SELECT id FROM persons WHERE unitid = ?)", (unitid,)
        )
        self.conn.execute("DELETE FROM persons WHERE unitid = ?", (unitid,))
        if self.progress:
            self.progress.update(stage="Enregistrement des contacts")
        for p in people:
            self._store_person(unitid, p, page_info, domain_for, patterns, form, hits.get(p["person_key"]))
        self.conn.execute(
            "INSERT INTO extraction_stats (unitid, raw_candidates, persons, pages_scanned) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (unitid) DO UPDATE SET raw_candidates = excluded.raw_candidates, "
            "persons = excluded.persons, pages_scanned = excluded.pages_scanned",
            (unitid, len(cands), len(people), len(rows)),
        )
        self.conn.commit()
        log.info("%s: %d pages scanned, %d raw matches, %d people kept", inst["name"], len(rows), len(cands), len(people))
        return len(people)

    def _profile_pages(self, people: List[dict], html_by_url: Dict[str, str], base: str, page_info: dict) -> None:
        """Open the profile page linked from a person's name to find their email / phone."""
        budget = self.s.max_profile_fetches_per_institution
        for p in people:
            if p["emails"] or budget <= 0:
                continue
            links: List[str] = []
            for role in p["roles"].values():
                for u in role["sources"]:
                    if u in html_by_url:
                        links += [l for l in profile_links(html_by_url[u], u, p["first_name"], p["last_name"])
                                  if registered_domain(urlparse(l).netloc) == base and l not in links]
            for link in links[:2]:
                if self.progress:
                    self.progress.check()
                budget -= 1
                row = self.crawler.fetch(link)
                if not row or not row.get("html"):
                    continue
                email, phone = parse_profile(row["html"], p["first_name"], p["last_name"])
                if email or phone:
                    page_info[link] = (row.get("source_date"), row.get("fetched_at"))
                    p.setdefault("profile_sources", []).append(link)
                    if email:
                        p["emails"].append((email, link))
                    if phone:
                        p["phones"].append((phone, link))
                    if email:
                        break

    def _store_person(self, unitid, p, page_info, domain_for, patterns, form: Optional[DirectoryForm],
                      hit: Optional[DirectoryHit] = None) -> None:
        reasons: List[str] = []
        email, email_status, email_source = "", "not found", ""
        if form and hit is not None:
            if not (hit.email or hit.phone):
                reasons.append("not found in the official directory search (may have left, or listed under another name)")
            elif hit.entries > 1:
                reasons.append(f"{hit.entries} directory entries share this name; email may belong to a namesake")
        if hit and hit.email:
            email, email_status = hit.email, "published"
            email_source = f"official directory search: {form.page_url}"
            if not name_in_email(hit.email, p["first_name"], p["last_name"]) and not any(
                    e == hit.email for e, _ in p["emails"]):
                reasons.append("directory email does not contain the person's name (e.g. maiden name); check it")
        elif p["emails"]:
            email, email_source = p["emails"][0]
            email_status = "published"
        else:
            hosts = [urlparse(u).netloc for role in p["roles"].values() for u in role["sources"]]
            choice = None  # (pattern info, domain, where)
            for h in hosts:  # format of the sub-site the person was found on, first
                d = domain_for(h)
                if (h, d) in self._host_patterns:
                    choice = (self._host_patterns[(h, d)], d, f" on {h}")
                    break
            if not choice:
                d = next((domain_for(h) for h in hosts if domain_for(h) in patterns), None)
                if d:
                    choice = (patterns[d], d, "")
            if choice:
                ex, domain, where = choice
                email = deduce(ex[0], p["first_name"], p["last_name"], domain)
            if email:
                email_status = "deduced"
                email_source = f"format {ex[0]}@{domain}{where}: seen on {ex[1]} of {ex[2]} published addresses"
                reasons.append("email deduced from the email format, not published")
        if email_status == "not found":
            reasons.append("no published email and no reliable email format for this institution")
        phone, phone_status, phone_source = "", "not found", ""
        if p["phones"]:
            phone, phone_source = p["phones"][0]
            phone_status = "published"
        elif hit and hit.phone:
            phone, phone_status, phone_source = hit.phone, "published", f"official directory search: {form.page_url}"

        ipeds_only = all(u.startswith("https://nces.ed.gov/") for r in p["roles"].values() for u in r["sources"])
        if not phone and any(u.startswith("https://nces.ed.gov/") for r in p["roles"].values() for u in r["sources"]):
            phone, phone_status = self._main_phone.get(unitid, ""), "main switchboard (IPEDS)"
            phone_source = IPEDS_SOURCE.format(unitid=unitid)
            if not phone:
                phone_status = "not found"
        if ipeds_only:
            reasons.append("from the federal IPEDS directory (2024 data), not seen on the website; confirm still in role")
        all_sources: List[str] = []
        dates: List[str] = []
        verified: List[str] = []
        for pid, role in p["roles"].items():
            if role["quality"] == "close":
                reasons.append(f"'{PROFILE_BY_ID[pid].target_title}' is only the closest match to the title found")
            elif pid == "other":
                reasons.append("no one matching a target profile at this institution; listed as other staff")
            elif role["quality"] == "fallback":
                reasons.append(f"no '{PROFILE_BY_ID[pid].target_title}' found; this is the closest title at this institution")
            if role.get("competitors"):
                reasons.append(f"{role['competitors'] + 1} people match '{PROFILE_BY_ID[pid].target_title}'")
            for u in role["sources"]:
                if u not in all_sources:
                    all_sources.append(u)
        for u in p.get("profile_sources", []):
            if u not in all_sources:
                all_sources.append(u)
        for u in all_sources:
            sd, fetched = page_info.get(u, (None, None))
            if sd:
                dates.append(sd)
            if fetched:
                verified.append(fetched[:10])
        if all(NEWS.search(u) for u in all_sources):
            reasons.append("only found in news/press pages; confirm the person is still in this role")
        newest = max(dates) if dates else None
        if newest and (date.today() - datetime.strptime(newest, "%Y-%m-%d").date()).days > self.s.stale_source_days:
            reasons.append(f"newest source is dated {newest} (older than {self.s.stale_source_days // 365} years)")

        if p["duties"]:
            resp = p["duties"][0] + " (from source page)"
        else:
            main_pid = sorted(p["roles"], key=lambda x: QUALITY_RANK[p["roles"][x]["quality"]])[0]
            resp = PROFILE_BY_ID[main_pid].duties + " (typical scope for this title)"

        first_src = all_sources[0] if all_sources else ""
        unit = unit_from_title(self._titles.get(first_src, ""), urlparse(first_src).netloc)
        level = contact_level(self._inst, all_sources, self._titles)
        if level.startswith("network"):
            unit = "Network headquarters / all campuses"
            reasons.append("found on the network's shared website, not on this campus's page: probably based "
                           "at headquarters or another campus")

        cur = self.conn.execute(
            "INSERT INTO persons (unitid, person_key, unit, level, first_name, last_name, email, email_status, email_source, "
            "phone, phone_status, phone_source, responsibilities, last_verified_date, manual_check, check_reasons) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (unitid, p["person_key"], unit, level, p["first_name"], p["last_name"], email, email_status, email_source,
             phone, phone_status, phone_source, resp, max(verified) if verified else None,
             1 if reasons else 0, "; ".join(dict.fromkeys(reasons))),
        )
        pid_row = cur.lastrowid
        if self.progress:
            self.progress.incr("contacts")
            if email_status == "published":
                self.progress.incr("emails_published")
        for pid, role in p["roles"].items():
            for u in role["sources"]:
                self.conn.execute(
                    "INSERT OR IGNORE INTO person_roles (person_id, profile_id, found_title, match_quality, "
                    "source_url, source_date) VALUES (?, ?, ?, ?, ?, ?)",
                    (pid_row, pid, pick_title(role["titles"]), role["quality"], u, page_info.get(u, (None,))[0]),
                )


def _page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:30000], re.I | re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(m.group(1))).strip() if m else ""
