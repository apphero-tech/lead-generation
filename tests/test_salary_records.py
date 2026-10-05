from leadgen.salary_records import SalaryRecords, match_employer, parse_listing

SITE = "https://transparentnevada.com"


def table(rows, next_page=None):
    body = "".join(f'<tr><td><a href="/salaries/x/{n.lower().replace(" ", "-")}">{n}</a></td><td>{t}</td><td>—</td></tr>'
                   for n, t in rows)
    more = f'<a href="?page={next_page}">next</a>' if next_page else ""
    return f"<table><tr><th>Name</th><th>Job Title</th><th>Department/Employer</th></tr>{body}</table>{more}"


class FakeCrawler:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, refresh=False):
        html = self.pages.get(url)
        return {"html": html} if html else None


def test_match_employer_ignores_university_college_and_of():
    slugs = {"nevada-state-college": [2021], "university-nevada-las-vegas": [2021], "college-southern-nevada": [2021]}
    assert match_employer("Nevada State University", slugs) == "nevada-state-college"
    assert match_employer("University of Nevada-Las Vegas", slugs) == "university-nevada-las-vegas"
    assert match_employer("Nevada State High School", slugs) is None


def test_parse_listing():
    rows = parse_listing(table([("Thomas E Nicholas", "Director of Workforce Development")]), SITE)
    assert rows == [("Thomas E Nicholas", "Director of Workforce Development", SITE + "/salaries/x/thomas-e-nicholas")]


def test_system_list_attributed_to_campus_only_for_known_people():
    sitemap = ("<loc>https://transparentnevada.com/salaries/2021/nevada-state-college</loc>"
               "<loc>https://transparentnevada.com/salaries/2025/nevada-system-higher-education-system-administrati</loc>")
    pages = {
        SITE + "/salaries/sitemap.xml": sitemap,
        SITE + "/salaries/2021/nevada-state-college": table([("Thomas E Nicholas", "Director"), ("Old Timer", "Registrar")]),
        SITE + "/salaries/2025/nevada-system-higher-education-system-administrati":
            table([("Thomas E Nicholas", "Director of Workforce Development")], next_page=2),
        SITE + "/salaries/2025/nevada-system-higher-education-system-administrati?page=2":
            table([("Other Campus", "Vice President for Advancement")]),
    }
    sr = SalaryRecords(FakeCrawler(pages))
    sr.sources = {"NV": {"site": SITE, "system_employers": ["nevada-system-higher-education-system-administrati"]}}
    cands, years = sr.candidates({"state": "NV", "name": "Nevada State University", "is_system": 0})
    got = [(c.first_name, c.last_name, c.profile_id, c.found_title) for c in cands]
    # Current (2025) title for the campus's own person; another campus's VP is never imported, and
    # someone only in the old 2021 list (left since) is not either.
    assert got == [("Thomas", "Nicholas", "wf_dir", "Director of Workforce Development")]
    assert set(years.values()) == {2025}


def test_state_without_source_does_nothing():
    sr = SalaryRecords(FakeCrawler({}))
    assert sr.candidates({"state": "TX", "name": "X University", "is_system": 0}) == ([], {})
