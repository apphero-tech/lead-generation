"""The 18 target profiles (head of institution + the 17 requested) and the title-matching rules.

Each profile has "exact" patterns (title clearly is that job) and "close" patterns (nearest
equivalent when the exact job does not exist). Patterns run on a normalised, lower-cased title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

NOT_AVP = r"(?<!associate )(?<!assistant )(?<!deputy )(?<!vice )"
# "Development" meaning something other than fundraising.
OTHER_DEVELOPMENT = (
    r"\b(workforce|professional|business|economic|software|leadership|curriculum|faculty|"
    r"organizational|career|product|staff|community|program|research|web|application|"
    r"land|real estate|youth|child|student|human|talent|systems?|course|training|"
    r"instructional|policy|energy|storage|corporate|strategy|racing) development\b"
)


@dataclass(frozen=True)
class Profile:
    id: str
    family: str
    target_title: str
    exact: Tuple[str, ...]
    close: Tuple[str, ...] = ()
    exclude: Tuple[str, ...] = ()
    duties: str = ""
    # Junior titles used only when the institution has nobody closer (typical of small schools).
    fallback: Tuple[str, ...] = ()


PROFILES: List[Profile] = [
    # LEADERSHIP (head of the institution: always useful, and often the only name at small schools)
    Profile(
        "head", "Leadership", "Head of Institution (President / Director)",
        exact=(r"^(president|chancellor|campus president|president and (ceo|chief executive officer)|"
               r"ceo|chief executive officer|executive director|campus director|school director|director|"
               r"superintendent|head of school|college president|university president)$",
               r"^(president|chancellor)(,| of) (the )?[a-z .'-]{0,60}(university|college|institute|school)$"),
        exclude=(r"\b(vice|associate|assistant|deputy|student|alumni|foundation|board|senate|association)\b",),
        duties="Chief executive of the institution: sets strategy and signs off on major investments and partnerships.",
    ),
    # ENROLLMENT / ADMISSIONS
    Profile(
        "enr_vp", "Enrollment / Admissions", "VP Enrollment Management",
        exact=(NOT_AVP + r"\bvice (president|provost|chancellor)\b[^.]{0,60}\benrollment",
               r"\bchief enrollment (officer|strategist)\b"),
        close=(r"\bdean of enrollment\b",),
        duties="Leads institution-wide enrollment strategy: recruitment, admissions, financial aid and retention.",
    ),
    Profile(
        "enr_avp", "Enrollment / Admissions", "Associate VP Enrollment Management",
        exact=(r"\b(associate|assistant) vice (president|provost|chancellor)\b[^.]{0,60}\benrollment",),
        duties="Supports the enrollment VP and runs part of the enrollment operation (admissions, recruitment or student financial services).",
    ),
    Profile(
        "enr_dir", "Enrollment / Admissions", "Director of Enrollment Management",
        exact=(r"\bdirector\b[^.]{0,40}\benrollment (management|services|strategy)",),
        close=(r"\bdirector\b[^.]{0,40}\benrollment\b", r"\bdean\b[^.]{0,30}\benrollment\b"),
        exclude=(r"\b(associate|assistant) director\b",),
        duties="Manages enrollment operations and goals, coordinating recruitment, admissions and enrollment services.",
    ),
    Profile(
        "adm_dir", "Enrollment / Admissions", "Director of Admissions",
        exact=(r"\bdirector\b[^.]{0,30}\badmissions?\b", r"\bdean of admissions?\b"),
        close=(r"\bdirector\b[^.]{0,30}\b(recruitment|recruiting)\b",),
        exclude=(r"\b(associate|assistant) director\b",),
        fallback=(r"\b(admissions?|enrollment|recruitment) (representative|coordinator|advisor|counselor|specialist|manager|officer)\b",),
        duties="Runs the admissions office: recruitment, application review and admission decisions.",
    ),
    Profile(
        "registrar", "Enrollment / Admissions", "Registrar",
        exact=(r"(?<!associate )(?<!assistant )(?<!deputy )\b(university |college )?registrar\b",),
        close=(r"\b(associate|deputy) registrar\b",),
        exclude=(r"\boffice of the registrar\b$",),
        fallback=(r"\bassistant registrar\b",
                  r"\b(student|academic) records (coordinator|specialist|manager|officer)\b"),
        duties="Oversees student records, registration, academic calendar, transcripts and degree certification.",
    ),
    # ADVANCEMENT / FOUNDATION
    Profile(
        "adv_vp", "Advancement / Foundation", "VP Advancement",
        exact=(NOT_AVP + r"\bvice (president|chancellor)\b[^.]{0,60}\b(advancement|development|alumni)",),
        exclude=(OTHER_DEVELOPMENT,),
        duties="Leads fundraising, alumni relations and advancement operations for the institution.",
    ),
    Profile(
        "adv_cao", "Advancement / Foundation", "Chief Advancement Officer",
        exact=(r"\bchief advancement officer\b",),
        close=(r"\bchief development officer\b", r"\bchief philanthropy officer\b"),
        duties="Senior executive responsible for the institution's fundraising and advancement strategy.",
    ),
    Profile(
        "adv_avp", "Advancement / Foundation", "Associate VP Advancement",
        exact=(r"\b(associate|assistant) vice (president|chancellor)\b[^.]{0,60}\b(advancement|development|alumni)",),
        exclude=(OTHER_DEVELOPMENT,),
        duties="Leads a major area of advancement (e.g. major gifts, development or advancement operations) under the VP.",
    ),
    Profile(
        "fdn_ed", "Advancement / Foundation", "Executive Director, University Foundation",
        exact=(r"\bexecutive director\b[^.]{0,60}\bfoundation\b",
               r"(?<!vice )\b(president|ceo|chief executive officer)\b[^.]{0,40}\bfoundation\b"),
        exclude=(r"\bassistant\b", r"\bfoundation (board|trustees?|relations)\b", r"\bcorporate (and )?foundation\b"),
        duties="Runs the institution's fundraising foundation: board relations, gift management and campaigns.",
    ),
    Profile(
        "adv_svc", "Advancement / Foundation", "Director of Advancement Services",
        exact=(r"\bdirector\b[^.]{0,40}\badvancement (services|operations)\b",),
        close=(r"\bdirector\b[^.]{0,40}\b(development|donor|gift) (services|operations|records|systems)\b",
               r"\bdirector\b[^.]{0,40}\badvancement (systems|information|data)\b"),
        exclude=(r"\b(associate|assistant) director\b",),
        duties="Manages advancement data, gift processing, donor records and fundraising systems (often the advancement CRM).",
    ),
    Profile(
        "dev_dir", "Advancement / Foundation", "Director of Development",
        exact=(r"\bdirector\b[^.]{0,30}\b(development|major gifts)\b",),
        exclude=(r"\b(associate|assistant) director\b",
                 OTHER_DEVELOPMENT, r"\bapplications?\b", r"\bdevelopment communications\b"),
        fallback=(r"\b(development|advancement|fundraising|annual giving|major gifts?) (officer|coordinator|manager|associate|specialist)\b",),
        duties="Raises funds from individual and institutional donors, typically for a college, unit or campaign.",
    ),
    Profile(
        "alu_dir", "Advancement / Foundation", "Director of Alumni Relations",
        exact=(r"\bdirector\b[^.]{0,30}\balumni\b",),
        close=(r"\b(president|ceo)\b[^.]{0,30}\balumni association\b",),
        exclude=(r"\b(associate|assistant) director\b",),
        fallback=(r"\balumni (relations |engagement )?(coordinator|manager|officer|specialist)\b",),
        duties="Leads alumni engagement: events, alumni association, communications and volunteer programs.",
    ),
    # IT / CRM
    Profile(
        "cio", "IT / CRM", "CIO (Chief Information Officer)",
        exact=(r"(?<!associate )(?<!assistant )(?<!deputy )\bchief information officer\b",
               r"(?<!associate )(?<!assistant )(?<!deputy )\bcio\b",
               NOT_AVP + r"\bvice (president|chancellor|provost)\b[^.]{0,40}\binformation technology\b"),
        close=(r"\bchief (technology|digital) officer\b",
               r"\b(executive )?director\b[^.]{0,20}\binformation technology\b"),
        exclude=(r"\b(associate|assistant) director\b",),
        fallback=(r"\b(information technology|technology|technical services) (manager|coordinator|administrator|specialist|lead)\b",
                  r"\b(network|systems?) (administrator|manager)\b"),
        duties="Leads the institution's information technology strategy, infrastructure and enterprise systems.",
    ),
    Profile(
        "is_dir", "IT / CRM", "Director of Information Systems / Enterprise Applications",
        exact=(r"\bdirector\b[^.]{0,40}\b(information systems|enterprise (applications?|systems|application services)|"
               r"administrative (systems|computing)|erp)\b",),
        close=(r"\bdirector\b[^.]{0,40}\b(application development|business systems|student information systems|"
               r"applications|integrations)\b",),
        exclude=(r"\b(associate|assistant) director\b",),
        duties="Manages enterprise applications (ERP, student information system) and the teams that support them.",
    ),
    Profile(
        "crm_dir", "IT / CRM", "Director of CRM / CRM Manager",
        exact=(r"\b(director|manager|administrator|lead|architect)\b[^.]{0,40}\b(crm|customer relationship management)\b",
               r"\bcrm (director|manager|administrator|lead|architect)\b"),
        close=(r"\b(director|manager|administrator)\b[^.]{0,40}\b(salesforce|slate)\b",
               r"\b(salesforce|slate) (administrator|manager|director|architect)\b",
               r"\b(crm|salesforce|slate)\b[^.]{0,30}\b(developer|engineer|analyst)\b"),
        duties="Owns the CRM platform (e.g. Salesforce or Slate): configuration, data, integrations and user support.",
    ),
    # CONTINUING EDUCATION / WORKFORCE
    Profile(
        "ce_dir", "Continuing Education / Workforce", "Director of Continuing Education / Executive Education",
        exact=(r"\b(director|dean|vice president|vice provost|executive director)\b[^.]{0,50}\b(continuing (education|studies)|"
               r"executive education|professional education|extended (learning|studies|education)|"
               r"professional (and|&) continuing|lifelong learning|professional studies)\b",),
        exclude=(r"\b(associate|assistant) director\b",),
        fallback=(r"\b(continuing|community|adult|professional) education (coordinator|manager|specialist|advisor)\b",),
        duties="Leads non-degree and professional programs: continuing education, executive and extended learning.",
    ),
    Profile(
        "wf_dir", "Continuing Education / Workforce", "Director of Workforce Development",
        exact=(r"\b(director|dean|vice president|vice provost|executive director|provost)\b[^.]{0,50}\bworkforce\b",),
        exclude=(r"\b(associate|assistant) director\b",),
        fallback=(r"\b(internship|apprenticeship|career services|workforce|career|employer relations) (coordinator|manager|specialist|advisor)\b",),
        duties="Leads workforce training programs, employer partnerships and career/technical education.",
    ),
]

# Last resort when an institution shows nobody for any target profile: any named staff member.
OTHER = Profile(
    "other", "Other staff", "Other staff (no target role found)", exact=(),
    duties="Staff member named on the institution's website; no one there matches a target profile.",
)
PROFILES.append(OTHER)

PROFILE_BY_ID = {p.id: p for p in PROFILES}

# A line must contain one of these words to be considered a job title at all.
TITLE_GATE = re.compile(
    r"\b(president|vice|director|dean|chief|officer|registrar|provost|manager|head|executive|"
    r"cio|ceo|administrator|chancellor|architect|lead|crm|salesforce|slate)\b"
)
# Support-staff titles that mention a boss's title but are not the boss.
NOT_A_LEADER = re.compile(
    r"\b(executive assistant|administrative assistant|assistant to|chief of staff|coordinator|"
    r"specialist|intern|student assistant|office manager|program assistant|receptionist)\b"
)

# Text that reads like a sentence rather than a job title.
SENTENCE = re.compile(
    r"\b(will|is|are|was|were|must|has|have|had|please|closed|can|may|our|your|we|you|click|contact us)\b"
)

HEADING = re.compile(r"\b(leadership|team|staff|council|cabinet|directory|members|organization)\s*$")

FORMER = re.compile(r"\b(former|retired|emerit\w*|past|late)\b")
SUPPORT_STAFF = re.compile(r"\b(assistant to|executive assistant|administrative assistant|student assistant|student worker)\b")

_COMPILED = [
    (p, [re.compile(x) for x in p.exact], [re.compile(x) for x in p.close], [re.compile(x) for x in p.exclude],
     [re.compile(x) for x in p.fallback])
    for p in PROFILES
]


def normalize_title(title: str) -> str:
    t = re.sub(r"\bIT\b", "information technology", title)
    t = t.replace("&", " and ").replace("’", "'")
    t = re.sub(r"\bV\.?P\.?\b", "vice president", t)
    t = re.sub(r"\bAVP\b", "associate vice president", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    t = re.sub(r"\bvp\b", "vice president", t)
    return t


def match_title(title: str) -> List[Tuple[Profile, str]]:
    """Return every (profile, 'exact'|'close'|'fallback') the title matches; empty if none.

    'fallback' = a junior title kept only when the institution has nobody closer for that profile.
    """
    if not title or len(title) > 160:
        return []
    t = normalize_title(title)
    if SENTENCE.search(t) or len(t.split()) > 18 or t.startswith("the ") and len(t.split()) > 3:
        return []
    if HEADING.search(t) or SUPPORT_STAFF.search(t) or FORMER.search(t):
        return []  # "CIO Senior Leadership" is a section heading; assistants are not the boss
    # Leader titles name the job up front ("Support staff ... of the Registrar" does not).
    leader = (bool(TITLE_GATE.search(t)) and not NOT_A_LEADER.search(t)
              and bool(TITLE_GATE.search(" ".join(t.replace("/", " ").split()[:4]))))
    out: List[Tuple[Profile, str]] = []
    for profile, exact, close, exclude, fallback in _COMPILED:
        if any(rx.search(t) for rx in exclude):
            continue
        if leader and any(rx.search(t) for rx in exact):
            out.append((profile, "exact"))
        elif leader and any(rx.search(t) for rx in close):
            out.append((profile, "close"))
        elif any(rx.search(t) for rx in fallback):
            out.append((profile, "fallback"))
    return out


def best_profile_quality(matches: List[Tuple[Profile, str]], profile_id: str) -> Optional[str]:
    for p, q in matches:
        if p.id == profile_id:
            return q
    return None


# Job words for the last-resort pass, most senior first (used to rank who is listed).
STAFF_RANKS = [
    r"\b(president|chancellor|provost|principal|superintendent|ceo|director|dean)\b",
    r"\b(chief|head|manager|supervisor|chair|administrator|registrar|bursar|controller|treasurer)\b",
    r"\b(coordinator|officer|lead|liaison|recruiter|representative)\b",
    r"\b(counselor|counsellor|advisor|adviser|specialist|analyst|librarian|accountant|secretary)\b",
    r"\b(assistant|associate)\b",
]
_STAFF_RANKS = [re.compile(x) for x in STAFF_RANKS]
NOT_STAFF = re.compile(r"\b(student|graduate|class of|intern|volunteer|trustee|board member|patient|parent)\b")
# The last-resort list stays within the target families: enrollment/admissions, advancement,
# IT/CRM, continuing education/workforce, and senior leadership. Libraries, athletics, faculty...
# are never listed.
RELEVANT_STAFF = re.compile(
    r"\b(admissions?|enrollment|recruit\w*|registrar|records|financial aid|advancement|development|"
    r"fundrais\w*|foundation|alumni|donor|gifts?|giving|philanthropy|information technology|technology|"
    r"information systems|systems|crm|salesforce|slate|erp|data|continuing|professional (education|studies)|"
    r"executive education|extended|workforce|career|internship|apprenticeship|training|"
    r"president|chancellor|provost|chief|vice president|cabinet|executive director)\b")
IRRELEVANT_STAFF = re.compile(
    r"\b(library|librar\w+|athletic\w*|coach|sports?|nurs\w+|counseling center|faculty|professor|"
    r"instructor|lecturer|teacher|liaison areas|custodian|facilities|police|dining|housing|chaplain)\b")


def staff_rank(title: str) -> Optional[int]:
    """0 (most senior) .. 4 if the title is a staff job title, else None."""
    if not title or len(title) > 120:
        return None
    t = normalize_title(title)
    if SENTENCE.search(t) or HEADING.search(t) or NOT_STAFF.search(t) or len(t.split()) > 12:
        return None
    if IRRELEVANT_STAFF.search(t) or not RELEVANT_STAFF.search(t):
        return None
    for rank, rx in enumerate(_STAFF_RANKS):
        if rx.search(t):
            return rank
    return None


def match_staff(title: str) -> List[Tuple[Profile, str]]:
    return [(OTHER, "fallback")] if staff_rank(title) is not None else []
