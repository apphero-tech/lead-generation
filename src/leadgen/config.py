"""Runtime settings. Override any value with an environment variable LEADGEN_<NAME>."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    db_path: Path = PROJECT_ROOT / "data" / "leadgen.db"
    out_dir: Path = PROJECT_ROOT / "out"
    log_dir: Path = PROJECT_ROOT / "logs"
    ipeds_dir: Path = PROJECT_ROOT / "data" / "ipeds"
    # IPEDS "HD" (directory) file year; HD2024 is the latest published as of 2026-10.
    ipeds_year: int = 2024

    # Keep it plain: some firewalls reject agents mentioning "robots.txt" (seen on Keiser, ATOM).
    user_agent: str = "LeadgenResearchBot/0.1 (+https://github.com/apphero-tech/lead-generation)"
    request_timeout: float = 20.0
    # Minimum seconds between two requests to the same host (robots.txt Crawl-delay wins if larger).
    per_host_delay: float = 1.0
    max_pages_per_institution: int = 400
    max_pages_per_host: int = 30
    max_depth: int = 4
    max_page_bytes: int = 3_000_000

    # Keep at most this many people per target profile per institution (0 = keep everyone, e.g.
    # one Director of Development per college).
    max_candidates_per_profile: int = 0
    # When nobody matches any target profile, list up to this many other named staff members.
    broad_max_people: int = 15
    # Profile pages opened (name links) for people without an email, per institution.
    max_profile_fetches_per_institution: int = 80
    # Look people up in the institution's own directory search form when one is found.
    use_directory_search: bool = True
    # A source older than this is flagged for manual check.
    stale_source_days: int = 730
    # Email format deduction: minimum examples and minimum share of the dominant pattern.
    pattern_min_examples: int = 3
    pattern_min_share: float = 0.6

    # Web interface: password required when set (always set it before sharing a link).
    ui_password: str = ""
    ui_user: str = "apphero"

    extra: dict = field(default_factory=dict)


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings() -> Settings:
    load_dotenv()
    s = Settings()
    for f in fields(s):
        env = os.environ.get("LEADGEN_" + f.name.upper())
        if env is None or f.name == "extra":
            continue
        current = getattr(s, f.name)
        if isinstance(current, Path):
            setattr(s, f.name, Path(env))
        elif isinstance(current, bool):
            setattr(s, f.name, env.lower() in ("1", "true", "yes"))
        elif isinstance(current, int):
            setattr(s, f.name, int(env))
        elif isinstance(current, float):
            setattr(s, f.name, float(env))
        else:
            setattr(s, f.name, env)
    return s
