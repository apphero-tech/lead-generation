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
