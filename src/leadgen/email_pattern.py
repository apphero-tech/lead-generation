"""Detect an institution's email format from published (name, email) pairs, and apply it.

An email built from the format is only ever labelled "deduced", never "published".
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from .names import strip_accents

PATTERNS: Dict[str, str] = {
    "first.last": "{first}.{last}",
    "firstlast": "{first}{last}",
    "first_last": "{first}_{last}",
    "first-last": "{first}-{last}",
    "flast": "{f}{last}",
    "f.last": "{f}.{last}",
    "firstl": "{first}{l}",
    "first.l": "{first}.{l}",
    "last.first": "{last}.{first}",
    "lastfirst": "{last}{first}",
    "lastf": "{last}{f}",
}
# "first@" and "last@" are deliberately absent: too ambiguous ("rob@" could be anyone named Rob).

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _clean(s: str) -> str:
    return re.sub(r"[^a-z]", "", strip_accents(s).lower())


def render(pattern: str, first: str, last: str) -> str:
    f, l = _clean(first), _clean(last)
    if not f or not l:
        return ""
    return PATTERNS[pattern].format(first=f, last=l, f=f[0], l=l[0])


def patterns_for(first: str, last: str, local_part: str) -> List[str]:
    """All formats that turn this name into this local part (digits at the end are tolerated)."""
    local = re.sub(r"\d+$", "", local_part.lower())
    return [p for p in PATTERNS if render(p, first, last) == local]


def email_matches_name(email: str, first: str, last: str) -> bool:
    """True if the email plausibly belongs to that person (used to attach published emails)."""
    local = email.split("@")[0].lower()
    if patterns_for(first, last, local):
        return True
    # Unusual formats (e.g. "jane.m.doe"): require both the full first and last name.
    f, l = _clean(first), _clean(last)
    letters = re.sub(r"[^a-z]", "", local)
    return len(f) >= 3 and len(l) >= 3 and f in letters and l in letters


def detect_pattern(
    pairs: Iterable[Tuple[str, str, str]], min_examples: int, min_share: float
) -> Optional[Tuple[str, int, int, List[str]]]:
    """pairs = (first, last, email) for ONE domain. Returns (pattern, support, total, examples) or None.

    "total" counts every reliable pair, including those that fit no known pattern, so a domain with
    arbitrary user IDs (e.g. jdoe42 vs. mary.smith) does not get a falsely confident pattern.
    """
    counts: Counter = Counter()
    examples: Dict[str, List[str]] = {}
    total = 0
    seen = set()
    for first, last, email in pairs:
        email = email.lower()
        if email in seen:
            continue
        seen.add(email)
        total += 1
        for p in patterns_for(first, last, email.split("@")[0]):
            counts[p] += 1
            examples.setdefault(p, []).append(email)
    if not counts:
        return None
    pattern, support = counts.most_common(1)[0]
    if support < min_examples or support / total < min_share:
        return None
    return pattern, support, total, examples[pattern][:5]


def deduce(pattern: str, first: str, last: str, domain: str) -> str:
    local = render(pattern, first, last)
    return f"{local}@{domain}" if local else ""


def name_in_email(email: str, first: str, last: str) -> bool:
    """Loose check: the address contains the first name, or (the start of) the last name."""
    letters = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    f, l = _clean(first), _clean(last)
    return (len(f) >= 3 and f in letters) or (len(l) >= 3 and l[:3] in letters)
