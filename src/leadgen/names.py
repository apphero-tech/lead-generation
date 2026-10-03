"""Person-name parsing and normalisation (used for extraction and de-duplication)."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import FrozenSet, Optional, Tuple

HONORIFICS = re.compile(r"^(dr|mr|mrs|ms|miss|prof|professor|rev|hon)\.?\s+", re.I)
CREDENTIALS = re.compile(
    r"(,?\s+(jr|sr|ii|iii|iv|ph\.?\s?d|ed\.?\s?d|m\.?b\.?a|m\.?s|m\.?a|m\.?ed|j\.?d|c\.?p\.?a|cfre|"
    r"sphr|pmp|dba|md|rn|esq)\.?)+\s*$",
    re.I,
)
TOKEN = re.compile(r"^(?:[^\W\d_][\w'’\-]*|[^\W\d_]\.)$", re.U)

# Words that show the text is not a person's name.
STOP_WORDS = {
    "university", "college", "school", "office", "department", "center", "centre", "institute", "vice",
    "president", "director", "dean", "provost", "chancellor", "chief", "officer", "manager", "registrar",
    "services", "management", "enrollment", "admissions", "advancement", "development", "foundation",
    "alumni", "information", "technology", "systems", "education", "workforce", "continuing", "the", "of",
    "and", "for", "to", "in", "at", "contact", "staff", "about", "home", "directory", "news", "events",
    "florida", "state", "student", "students", "affairs", "academic", "executive", "associate",
    "assistant", "senior", "interim", "relations", "programs", "program", "research", "campus", "board",
    "trustees", "cabinet", "leadership", "team", "our", "meet", "us", "view", "read", "more", "learn",
    "email", "phone", "fax", "click", "here", "apply", "give", "giving", "online", "health", "human",
    "resources", "finance", "operations", "strategic", "communications", "marketing", "general",
    "counsel", "athletics", "library", "libraries", "faculty", "council", "committee", "division",
    "global", "international", "community", "public", "national", "american", "annual", "fund",
    "capital", "campaign", "gift", "gifts", "planned", "major", "data", "records", "analytics",
    "chair", "secretary", "treasurer", "member", "members", "welcome", "message", "profile", "bio",
    "biography", "search", "menu", "skip", "main", "content", "page", "site", "privacy", "policy",
    "copyright", "rights", "reserved", "map", "maps", "calendar", "jobs", "careers", "support",
}


@lru_cache(maxsize=1)
def first_names() -> FrozenSet[str]:
    text = resources.files("leadgen").joinpath("first_names.txt").read_text(encoding="utf-8")
    return frozenset(l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#"))


@lru_cache(maxsize=1)
def dictionary_words() -> FrozenSet[str]:
    """Lower-case English words from the system dictionary (macOS/Linux); empty if unavailable."""
    path = Path("/usr/share/dict/words")
    if not path.exists():
        return frozenset()
    return frozenset(w for w in path.read_text(encoding="utf-8", errors="ignore").split() if w.islower())


def is_common_word(token: str) -> bool:
    w = strip_accents(token).lower()
    words = dictionary_words()
    candidates = {w, re.sub(r"(ing|es|s|ed)$", "", w), re.sub(r"(ing|ed)$", "e", w)}
    return any(c in words for c in candidates if len(c) >= 3)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def parse_name(text: str) -> Optional[Tuple[str, str]]:
    """Return (first_name, last_name) if the text looks like a person's name, else None."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text).strip().strip(",;:|–-").strip()
    t = re.sub(r"\(.*?\)", "", t).strip()  # nicknames / pronouns in parentheses
    t = re.sub(r"[“\"].*?[”\"]", "", t).strip()  # quoted nicknames
    t = HONORIFICS.sub("", t)
    t = CREDENTIALS.sub("", t).strip().strip(",").strip()
    if not t or len(t) > 45:
        return None
    tokens = t.split(" ")
    if not 2 <= len(tokens) <= 4:
        return None
    for tok in tokens:
        if not TOKEN.match(tok):
            return None
        if not tok[0].isupper():
            return None
        if strip_accents(tok).lower().strip(".") in STOP_WORDS:
            return None
        if len(tok) > 2 and tok.isupper():
            return None  # ALL-CAPS words are headings/acronyms, not names
    words = [tok for tok in tokens if not re.match(r"^[^\W\d_]\.$", tok)]
    if len(words) < 2:
        return None
    first = strip_accents(words[0]).lower()
    # "Update Address", "Business Analyst": not a known first name and an ordinary English word.
    if first not in first_names() and is_common_word(words[0]):
        return None
    if first not in first_names() and all(is_common_word(w) for w in words[1:]):
        return None
    return words[0], words[-1]


def name_key(first: str, last: str) -> str:
    """De-duplication key: accent-free, lower-case first + last name (middle names ignored)."""
    def clean(s: str) -> str:
        return re.sub(r"[^a-z]", "", strip_accents(s).lower())
    return clean(first) + "|" + clean(last)
