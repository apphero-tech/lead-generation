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
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


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


def find_candidates(lines: List[str], url: str) -> List[Candidate]:
    out: List[Candidate] = []
    if NON_STAFF_PAGE.search(url):
        return out
    names_at = [line_name(l) for l in lines]
    for i, line in enumerate(lines):
        name, title = split_name_title(strip_contact(line))
        matches = match_title(title)
        if not matches:
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
    hi = min(len(lines), max(title_idx, name_idx) + 6)
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
