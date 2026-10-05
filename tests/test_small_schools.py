from leadgen.extract import Candidate
from leadgen.ipeds import format_us_phone
from leadgen.pipeline import ipeds_chief, merge_candidates
from leadgen.profiles import match_title


def ids(title):
    return {(p.id, q) for p, q in match_title(title)}


def cand(first, last, pid, q, url="https://s.edu/staff"):
    return Candidate(first, last, pid, q, "t", url)


def test_head_of_institution_titles():
    assert ("head", "exact") in ids("Director")
    assert ("head", "exact") in ids("President")
    assert ids("Vice President") == set() and ids("Student Body President") == set()


def test_fallback_titles():
    assert ("adm_dir", "fallback") in ids("Admissions Representative")
    assert ("wf_dir", "fallback") in ids("Internship Coordinator; Apprenticeship Coordinator")
    assert ("registrar", "fallback") in ids("Assistant Registrar")


def test_fallback_dropped_when_a_real_match_exists():
    people = merge_candidates([cand("Ann", "Lee", "adm_dir", "fallback"), cand("Bob", "Ray", "adm_dir", "exact")], 0)
    assert [p["last_name"] for p in people] == ["Ray"]
    people = merge_candidates([cand("Ann", "Lee", "adm_dir", "fallback")], 0)
    assert [p["last_name"] for p in people] == ["Lee"]  # nobody better: keep the closest title


def test_ipeds_chief_added_unless_site_shows_another_head():
    inst = {"unitid": "9", "chief_name": "Dr. Tara Scott", "chief_title": "President"}
    added = ipeds_chief(inst, [], ["<p>Welcome</p>"])
    assert [(c.first_name, c.last_name, c.profile_id) for c in added] == [("Tara", "Scott", "head")]
    other = [cand("Lee", "Ng", "head", "exact")]
    assert ipeds_chief(inst, other, ["<p>Welcome</p>"]) == []  # the website already shows a head


def test_big_institutions_keep_only_official_president():
    from leadgen.pipeline import filter_heads
    cs = [Candidate(n, "X", "head", "exact", "Director", "https://dept.u.edu/a") for n in ("Ann", "Bob", "Cy", "Dan")]
    cs.append(Candidate("Eve", "Bell", "head", "exact", "President", "https://www.u.edu/about/administration/"))
    kept = [c.first_name for c in filter_heads(cs, "https://www.u.edu/")]
    assert kept == ["Eve"]
    assert ("head", "exact") not in ids("Director, Finance")


def test_ipeds_phone_format():
    assert format_us_phone("9544000620") == "(954) 400-0620"
    assert format_us_phone("85098357003632") == "(850) 983-5700 ext. 3632"
    assert format_us_phone("-1") == ""


def test_broad_staff_when_no_target_role():
    from leadgen.pipeline import broad_staff
    html = ("<div><h3>Ann Lee</h3><p>Admissions Counselor</p></div><div><h3>Bob Ray</h3><p>Financial Aid Director</p></div>"
            "<div><h3>Cy Moe</h3><p>Student Ambassador</p></div><div><h3>Dee Fry</h3><p>Evening Librarian</p></div>"
            "<div><h3>Eli Park</h3><p>Athletics Director</p></div>")
    got = [(c.first_name, c.profile_id) for c in broad_staff([("https://s.edu/staff", html)], 15)]
    # Most senior first; students, library and athletics (outside the target families) excluded.
    assert got == [("Bob", "other"), ("Ann", "other")]


def test_ipeds_non_head_title_not_labelled_head():
    inst = {"unitid": "9", "chief_name": "Erica Amorim", "chief_title": "Chief Data Officer"}
    assert [c.profile_id for c in ipeds_chief(inst, [], [""])] == ["other"]
    inst = {"unitid": "9", "chief_name": "Arthur Keiser", "chief_title": "Chancellor"}
    assert [c.profile_id for c in ipeds_chief(inst, [], [""])] == ["head"]


def test_network_campus_level():
    from leadgen.pipeline import campus_terms, contact_level
    inst = {"name": "Arizona College of Nursing-Fort Lauderdale", "city": "Fort Lauderdale",
            "site_shared_by": "20", "chief_shared_by": "20"}
    assert "Fort Lauderdale" in campus_terms(inst)
    ipeds = "https://nces.ed.gov/collegenavigator/?id=1"
    assert contact_level(inst, [ipeds], {}).startswith("network-wide (20")  # same CEO for 20 campuses
    assert contact_level(inst, ["https://x.edu/leadership"], {}).startswith("network-wide")
    assert contact_level(inst, ["https://x.edu/campuses/fort-lauderdale/"], {}) == "this campus"
    assert contact_level({**inst, "site_shared_by": "1"}, ["https://x.edu/leadership"], {}) == "this campus"
    assert contact_level({**inst, "chief_shared_by": "1"}, [ipeds], {}) == "this campus"


def test_crawl_budget_is_spread_per_section():
    from leadgen.crawler import section_of
    assert section_of("https://nevadastate.edu/admissions/tours/") == "nevadastate.edu/admissions"
    assert section_of("https://it.ufl.edu/") == "it.ufl.edu/"


def test_president_outranks_bare_director():
    from leadgen.pipeline import filter_heads
    cs = [Candidate("Rik", "Gad", "head", "exact", "Director", "https://n.edu/a"),
          Candidate("Sandra", "Rich", "head", "exact", "President", "https://n.edu/about/leadership/")]
    assert [c.first_name for c in filter_heads(cs, "https://n.edu/")] == ["Sandra"]


def test_sitemaps_and_campus_maps():
    from leadgen.crawler import score_link
    assert score_link("https://n.edu/page-sitemap.xml", "") >= 0
    assert score_link("https://n.edu/campus-map/", "") == -1
    assert score_link("https://n.edu/maps/", "") == -1
    assert score_link("https://n.edu/unit/development-alumni/", "") > 0
