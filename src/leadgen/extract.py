"""Step 3: find target people on cached pages (rule-based, no paid AI).

A page is flattened into text lines. A line that matches a target title is paired with a person
name on the same line ("Jane Doe, Registrar") or on a neighbouring line (name above or below the
title). Emails are only attached when the address plausibly belongs to that person's name; phone
numbers only when no other person's name sits between the person and the number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup

from .email_pattern import EMAIL_RE, email_matches_name
from .names import parse_name
from .profiles import match_title

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)")
DUTY_VERBS = re.compile(r"\b(oversees|leads|responsible for|manages|directs|supervises|serves as|provides leadership)\b", re.I)
BLOCK_TAGS = ["p", "div", "li", "td", "th", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd",
              "section", "article", "header", "figcaption", "address", "br", "blockquote"]


# Pages listing people who do not work for the institution (boards, alumni, donors, awards).
NON_STAFF_PAGE = re.compile(
    r"(advisory|hall-of-fame|halloffame|distinguished|donor|honor-roll|in-memoriam|obituar|"
    r"board-of-(directors|trustees|advisors|visitors)|/trustees|ambassador|award|spotlight|"
    r"/alumni/?$|alumni-profiles|notable-alumni|class-notes|speakers?/|/students?/)",
    re.I,
)


@dataclass
class Candidate:
    first_name: str
    last_name: str
    profile_id: str
    match_quality: str
    found_title: str
    source_url: str
    email: str = ""
    phone: str = ""
    phone_note: str = ""
    duty_sentence: str = ""
    notes: List[str] = field(default_factory=list)


def page_lines(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "footer", "form", "svg", "iframe"]):
        t.decompose()
    # Make emails / phones hidden in links visible in the text.
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr and addr.lower() not in a.get_text().lower():
                a.append(f" <{addr}>")
        elif href.lower().startswith("tel:"):
            num = href[4:].strip()
            if num and not PHONE_RE.search(a.get_text()):
                a.append(f" tel:{num}")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*", ".", text, flags=re.I)
    lines = []
    for raw in text.split("\n"):
        # Icon-font glyphs (private-use characters) are decoration, not content.
        line = re.sub(r"\s+", " ", re.sub(r"[\ue000-\uf8ff]", "", raw)).strip()
        if line and re.search(r"[A-Za-z0-9]", line):
            lines.append(line)
    return merge_split_names(lines)


NAME_TOKEN = re.compile(r"^[A-Z][a-zA-Z'’\-]{1,30}$")


def merge_split_names(lines: List[str]) -> List[str]:
    """Directories that print 'Carroll' / 'Nicholas' / 'Nicholas.Carroll@x.edu' on separate lines:
    join the two name lines into 'Nicholas Carroll' when a nearby email confirms who it is."""
    out: List[str] = []
    i = 0
    while i < len(lines):
        a = lines[i]
        b = lines[i + 1] if i + 1 < len(lines) else ""
        if NAME_TOKEN.match(a) and NAME_TOKEN.match(b):
            near = [e for l in lines[i + 2:i + 5] for e in EMAIL_RE.findall(l)]
            if any(email_matches_name(e, b, a) for e in near):      # "Last" then "First"
                out.append(f"{b} {a}")
                i += 2
                continue
            if any(email_matches_name(e, a, b) for e in near):      # "First" then "Last"
                out.append(f"{a} {b}")
                i += 2
                continue
        out.append(a)
        i += 1
    return out


LEADING_TITLE = re.compile(r"^(President|Chancellor|Director|Executive Director|Campus Director|CEO)\s+(.+)$")


def split_name_title(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    """'Jane Doe, Vice President, Advancement' -> (('Jane','Doe'), 'Vice President, Advancement')."""
    m = LEADING_TITLE.match(line)
    if m and parse_name(m.group(2)):
        return parse_name(m.group(2)), m.group(1)  # "President Dr. Jane Doe"
    for sep in (", ", " - ", " – ", " — ", " | ", ": "):
        if sep in line:
            left, right = line.split(sep, 1)
            name = parse_name(left)
            if name:
                return name, right.strip()
            name = parse_name(right)  # 'Registrar: Jane Doe'
            if name:
                return name, left.strip()
    return None, line


def strip_contact(line: str) -> str:
    """Remove emails / phone numbers so 'Jane Doe <jd@x.edu>, Director' parses cleanly."""
    cleaned = re.sub(r"<?" + EMAIL_RE.pattern + r">?", "", line)
    cleaned = PHONE_RE.sub("", cleaned).replace("tel:", "")
    cleaned = re.sub(r"\b(email|e-mail|phone|tel|office)\s*:?\s*$", "", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip(" ,|-")


def line_name(line: str) -> Optional[Tuple[str, str]]:
    """Name if the whole line (minus an email/phone) is a person's name."""
    cleaned = strip_contact(line)
    name = parse_name(cleaned)
    if name:
        return name
    name, _ = split_name_title(cleaned)
    return name


def format_phone(m: re.Match) -> str:
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}"


# Bio sentences: "Dr. Jane B. Doe has served as President of X since 2024",
# "John Roe was named Vice President for Advancement in May", "Ann Lee joined X in 2020 as Registrar".
BIO_SENTENCE = re.compile(
    r"(?P<name>(?:(?:Dr|Mr|Ms|Mrs)\.\s+)?[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]*\.?){1,3})\s*,?\s+"
    r"(?:has served as|has been|serves as|is the|is|was named|was appointed|became|joined\s[^.]{0,60}?\sas)\s+"
    r"(?:the\s+|our\s+|an?\s+)?(?:new\s+|interim\s+)?"
    r"(?P<title>[A-Z][^.;:()]{1,110}?)(?=\s+(?:since|in|from|where|until|at)\s|\s+and\s+[a-z]|[.,;]|$)")
# "President of Nevada State University" -> "President" (the job, without the institution).
TITLE_TAIL = re.compile(r"\s+(?:of|at|for)\s+(?:the\s+)?[A-Z][\w .&'’-]*?"
                        r"(?:University|College|Institute|School|Academy|State|System)\b.*$")


def bio_candidates(line: str, url: str) -> List[Candidate]:
    out: List[Candidate] = []
    if len(line) > 500:
        return out
    for m in BIO_SENTENCE.finditer(line):
        name = parse_name(m.group("name"))
        title = TITLE_TAIL.sub("", m.group("title").strip())
        if not name:
            continue
        for profile, quality in match_title(title):
            out.append(Candidate(name[0], name[1], profile.id, quality, title, url))
    return out


BARE_TITLE = re.compile(r"^(senior |executive |associate |assistant |interim )?"
                        r"(vice president|vice chancellor|director|coordinator|manager|dean|officer)$", re.I)


def find_candidates(lines: List[str], url: str, matcher=match_title, context: str = "") -> List[Candidate]:
    """context: the page's own subject (e.g. "Career Services Center"), used to complete bare
    titles such as "Director" into "Director, Career Services Center"."""
    out: List[Candidate] = []
    if NON_STAFF_PAGE.search(url):
        return out
    names_at = [line_name(l) for l in lines]
    for i, line in enumerate(lines):
        name, title = split_name_title(strip_contact(line))
        if context and BARE_TITLE.match(title.strip(" ,")):
            completed = f"{title.strip(' ,')}, {context}"
            if matcher(completed):
                title = completed
        matches = matcher(title)
        if not matches:
            if matcher is match_title and len(line) > 40:
                out.extend(bio_candidates(line, url))
            continue
        name_idx = i
        if not name:
            # Name directly above the title (most cards), else directly below.
            for j in (i - 1, i - 2, i + 1):
                if 0 <= j < len(lines) and names_at[j]:
                    # Below-the-title names followed by "Assistant" text are support staff.
                    if j > i and j + 1 < len(lines) and "assistant" in lines[j + 1].lower():
                        continue
                    name, name_idx = names_at[j], j
                    break
        if not name:
            continue
        first, last = name
        email, phone, phone_note, duty = scan_context(lines, names_at, i, name_idx, first, last)
        for profile, quality in matches:
            c = Candidate(first, last, profile.id, quality, title.strip(" ,"), url,
                          email=email, phone=phone, phone_note=phone_note, duty_sentence=duty)
            out.append(c)
    return out


def scan_context(lines, names_at, title_idx, name_idx, first, last):
    """Look around the person for their email, phone and a sentence describing their role."""
    lo = max(0, min(title_idx, name_idx) - 2)
    hi = min(len(lines), max(title_idx, name_idx) + 8)
    email = ""
    for k in range(lo, hi):
        for addr in EMAIL_RE.findall(lines[k]):
            if email_matches_name(addr, first, last):
                email = addr.lower()
                break
        if email:
            break
    phone, note = "", ""
    start = min(title_idx, name_idx)
    for k in range(start, hi):
        other = names_at[k]
        if other and other != (first, last) and k > max(title_idx, name_idx):
            break  # someone else's block starts here; their phone is not ours
        m = PHONE_RE.search(lines[k])
        if m:
            phone = format_phone(m)
            if re.search(r"\bfax\b", lines[k], re.I):
                phone, note = "", ""
                continue
            break
    duty = ""
    for k in range(start, hi):
        line = lines[k]
        if 40 <= len(line) <= 400 and DUTY_VERBS.search(line):
            sentence = re.split(r"(?<=[.!?])\s+", line)
            hit = next((s for s in sentence if DUTY_VERBS.search(s)), "")
            if hit and 30 <= len(hit) <= 300:
                duty = hit.strip()
                break
    return email, phone, note, duty


def name_email_pairs(html: str) -> Iterable[Tuple[str, str, str]]:
    """Reliable (first, last, email) pairs for email-format detection: mailto links whose text,
    or the line just above, is a person's name."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith("mailto:"):
            continue
        email = href[7:].split("?")[0].strip().lower()
        if not EMAIL_RE.fullmatch(email):
            continue
        name = parse_name(a.get_text(" ", strip=True))
        if not name:
            # Card layout: name in a heading inside the same parent block.
            parent = a.find_parent(["li", "div", "td", "article", "section", "p"])
            if parent is not None and len(parent.get_text(" ", strip=True)) < 400:
                head = parent.find(["h2", "h3", "h4", "h5", "strong", "b"])
                if head is not None:
                    name = parse_name(head.get_text(" ", strip=True))
        if name:
            yield name[0], name[1], email


def profile_links(html: str, page_url: str, first: str, last: str) -> List[str]:
    """Links on a page whose text is this person's name (typically their bio / profile page)."""
    from urllib.parse import urljoin, urldefrag
    from .names import name_key
    want = name_key(first, last)
    full = f"{first} {last}".lower()
    out: List[str] = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        text = a.get_text(" ", strip=True)
        if not text or len(text) > 80:
            continue
        name = parse_name(text)
        if (name and name_key(*name) == want) or full in text.lower():
            link = urldefrag(urljoin(page_url, href))[0]
            if link.startswith("http") and link.rstrip("/") != page_url.rstrip("/") and link not in out:
                out.append(link)
    return out


def parse_profile(html: str, first: str, last: str) -> Tuple[str, str]:
    """(email, phone) from a person's own profile page: taken next to their name, and the email
    must plausibly belong to them (so footer / generic office addresses are ignored)."""
    from .email_pattern import name_in_email
    lines = page_lines(html)
    l_low = last.lower()
    starts = [i for i, line in enumerate(lines) if l_low in line.lower() and len(line) < 120]
    windows = [(i, min(len(lines), i + 25)) for i in starts[:3]] or [(0, len(lines))]
    email = phone = ""
    for lo, hi in windows:
        for k in range(lo, hi):
            for addr in EMAIL_RE.findall(lines[k]):
                addr = addr.lower()
                if not email and (email_matches_name(addr, first, last) or name_in_email(addr, first, last)):
                    email = addr
            m = PHONE_RE.search(lines[k])
            if m and not phone and starts and not re.search(r"\bfax\b", lines[k], re.I):
                phone = format_phone(m)
        if email:
            break
    return email, phone


def page_name_email_pairs(lines: List[str]) -> Iterable[Tuple[str, str, str]]:
    """Every (first, last, email) on a page where the email sits next to that person's name and
    plausibly belongs to them, whatever their job: evidence of the institution's email format."""
    for i, line in enumerate(lines):
        name = line_name(line)
        if not name:
            continue
        for k in range(i, min(len(lines), i + 5)):
            hit = next((a for a in EMAIL_RE.findall(lines[k]) if email_matches_name(a, *name)), None)
            if hit:
                yield name[0], name[1], hit.lower()
                break
