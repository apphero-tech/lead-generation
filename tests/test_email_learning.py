from leadgen.config import Settings
from leadgen.db import connect, now_iso
from leadgen.extract import parse_profile, profile_links
from leadgen.pipeline import Pipeline

STAFF = """
<div><h3>Ann Lee</h3><p>Director of Admissions</p><p>alee@s.edu</p></div>
<div><h3>Bob Ray</h3><p>Registrar</p><p>bray@s.edu</p></div>
<div><h3>Cara Moss</h3><p>Director of Alumni Relations</p><p>cmoss2@s.edu</p></div>
<div><h3>Dan Fox</h3><p>Director of Development</p></div>
"""


def test_format_learned_from_contacts_own_emails(tmp_path):
    s = Settings(db_path=tmp_path / "t.db", use_directory_search=False)
    conn = connect(s.db_path)
    conn.execute("INSERT INTO institutions (unitid, state, name, website, is_system) "
                 "VALUES ('1', 'FL', 'S College', 'https://www.s.edu/', 0)")
    conn.execute("INSERT INTO pages (url, host, status, fetched_at, html) VALUES (?, ?, 200, ?, ?)",
                 ("https://www.s.edu/staff/", "www.s.edu", now_iso(), STAFF))
    conn.execute("INSERT INTO institution_pages (unitid, url, depth) VALUES ('1', 'https://www.s.edu/staff/', 1)")
    conn.commit()
    inst = dict(conn.execute("SELECT * FROM institutions").fetchone())
    Pipeline(conn, s).extract_institution(inst)
    got = {r["last_name"]: (r["email"], r["email_status"]) for r in conn.execute("SELECT * FROM persons")}
    assert got["Lee"] == ("alee@s.edu", "published")
    assert got["Fox"] == ("dfox@s.edu", "deduced")  # format flast learned from the 3 contacts above


def test_profile_page_link_and_parse():
    page = '<li><a href="/people/dan-fox">Dan Fox</a> Director of Development</li>'
    assert profile_links(page, "https://www.s.edu/staff/", "Dan", "Fox") == ["https://www.s.edu/people/dan-fox"]
    profile = ('<footer>info@s.edu (555) 111-0000</footer><h1>Dan Fox</h1><p>Director of Development</p>'
               '<p><a href="mailto:dfox@s.edu">dfox@s.edu</a></p><p>Phone: 555-262-1234</p>')
    assert parse_profile(profile, "Dan", "Fox") == ("dfox@s.edu", "(555) 262-1234")


def test_format_learned_per_sub_site(tmp_path):
    s = Settings(db_path=tmp_path / "t.db", use_directory_search=False)
    conn = connect(s.db_path)
    conn.execute("INSERT INTO institutions (unitid, state, name, website, is_system) "
                 "VALUES ('1', 'FL', 'N University', 'https://www.n.edu/', 0)")
    giving = STAFF.replace("s.edu", "n.edu")  # flast on the giving site
    undergrad = """<div><h3>Eve Stone</h3><p>Director of Admissions</p><p>eve.stone@n.edu</p></div>
    <div><h3>Gus Hale</h3><p>Registrar</p><p>gus.hale@n.edu</p></div>
    <div><h3>Ida Lamb</h3><p>Director of Enrollment Management</p><p>ida.lamb@n.edu</p></div>"""
    for url, html in (("https://giving.n.edu/people/", giving), ("https://undergrad.n.edu/people/", undergrad)):
        conn.execute("INSERT INTO pages (url, host, status, fetched_at, html) VALUES (?, ?, 200, ?, ?)",
                     (url, url.split('/')[2], now_iso(), html))
        conn.execute("INSERT INTO institution_pages (unitid, url, depth) VALUES ('1', ?, 1)", (url,))
    conn.commit()
    Pipeline(conn, s).extract_institution(dict(conn.execute("SELECT * FROM institutions").fetchone()))
    got = {r["last_name"]: (r["email"], r["email_status"]) for r in conn.execute("SELECT * FROM persons")}
    # Across the university 3 flast vs 3 first.last: no global format, but giving.n.edu is clearly flast.
    assert got["Fox"] == ("dfox@n.edu", "deduced")


def test_format_learned_from_any_staff_on_the_site(tmp_path):
    s = Settings(db_path=tmp_path / "t.db", use_directory_search=False)
    conn = connect(s.db_path)
    conn.execute("INSERT INTO institutions (unitid, state, name, website, is_system) "
                 "VALUES ('1', 'NV', 'N State', 'https://n.edu/', 0)")
    library = """<div><h3>Lynn Best</h3><p>Evening Librarian</p><p>lynn.best@n.edu</p></div>
    <div><h3>Lisa Foster</h3><p>Librarian</p><p>lisa.foster@n.edu</p></div>
    <div><h3>Joel Gonzales</h3><p>Librarian</p><p>joel.gonzales@n.edu</p></div>"""
    careers = "<title>Career Services Center - N State</title><p>Director – Kaytee Johns, M.S.</p>"
    for url, html in (("https://n.edu/library/staff", library), ("https://n.edu/career-services-center/", careers)):
        conn.execute("INSERT INTO pages (url, host, status, fetched_at, html) VALUES (?, 'n.edu', 200, ?, ?)",
                     (url, now_iso(), html))
        conn.execute("INSERT INTO institution_pages (unitid, url, depth) VALUES ('1', ?, 1)", (url,))
    conn.commit()
    Pipeline(conn, s).extract_institution(dict(conn.execute("SELECT * FROM institutions").fetchone()))
    rows = {r["last_name"]: r for r in conn.execute("SELECT * FROM persons")}
    # Librarians are not contacts, but their addresses teach the format first.last@n.edu.
    assert "Best" not in rows
    assert (rows["Johns"]["email"], rows["Johns"]["email_status"]) == ("kaytee.johns@n.edu", "deduced")
    title = conn.execute("SELECT found_title FROM person_roles").fetchone()[0]
    assert title == "Director, Career Services Center"


def test_bare_vice_president_completed_with_office():
    from leadgen.extract import find_candidates, page_lines
    html = "<h3>Erin Keller</h3><p>Vice President</p><h3>Diana Morgan</h3><p>Director of Stewardship & Community Relations</p>"
    got = {(c.last_name, c.profile_id, c.found_title)
           for c in find_candidates(page_lines(html), "https://n.edu/unit/development-alumni/", context="Development and Alumni")}
    assert ("Keller", "adv_vp", "Vice President, Development and Alumni") in got
    assert any(x[0] == "Morgan" and x[1] == "dev_dir" for x in got)
