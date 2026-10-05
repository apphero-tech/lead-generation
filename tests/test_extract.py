from leadgen.extract import find_candidates, page_lines

CABINET = """
<div><h3>Jane Doe, Vice President, Enrollment Management</h3><p>Enrollment Management</p>
<p>Paula Smith</p><p>, Executive Assistant II</p><p>(555) 294-0981</p></div>
<div><h3>Rita Stone</h3><p>Vice President for Advancement</p>
<a href="mailto:rita.stone@u.edu">Email</a><p>Phone: 555-392-1111</p></div>
<div><h3>Mona Stone</h3><p>Executive Assistant III</p><a href="mailto:mona.stone@u.edu">Email</a></div>
"""


def test_cards():
    found = {(c.first_name, c.last_name, c.profile_id): c for c in find_candidates(page_lines(CABINET), "u")}
    assert ("Jane", "Doe", "enr_vp") in found
    assert found[("Jane", "Doe", "enr_vp")].phone == ""  # that phone belongs to the assistant
    m = found[("Rita", "Stone", "adv_vp")]
    assert m.email == "rita.stone@u.edu" and m.phone == "(555) 392-1111"
    assert not any(k[0] in ("Paula", "Mona") for k in found)


def test_junk_names_and_non_staff_pages():
    from leadgen.names import parse_name
    assert parse_name("Update Address") is None and parse_name("Business Analyst") is None
    assert parse_name("Naz Ozkan") == ("Naz", "Ozkan")
    html = "<h3>Kim Tran</h3><p>Vice President for Advancement</p>"
    assert find_candidates(page_lines(html), "https://u.edu/about/advisory-board/") == []
    assert find_candidates(page_lines(html), "https://u.edu/about/leadership/")


def test_name_line_with_email():
    html = "<p>Lena Watts <a href='mailto:lwatts@u.edu'>lwatts@u.edu</a>, Director, Development</p>"
    c = find_candidates(page_lines(html), "u")[0]
    assert (c.first_name, c.last_name, c.email) == ("Lena", "Watts", "lwatts@u.edu")


def test_title_before_name():
    c = find_candidates(page_lines("<p>President Dr. Stuart R. Bell</p>"), "u")[0]
    assert (c.first_name, c.last_name, c.profile_id) == ("Stuart", "Bell", "head")


def test_bio_sentences():
    from leadgen.extract import bio_candidates
    def got(t):
        return [(c.first_name, c.last_name, c.profile_id, c.found_title) for c in bio_candidates(t, "u")]
    assert got("Dr. Sandra B. Richtermeyer has served as President of Nevada State University since August 2026.") \
        == [("Sandra", "Richtermeyer", "head", "President")]
    assert got("Maria Gomez serves as Director of Admissions at the college.") \
        == [("Maria", "Gomez", "adm_dir", "Director of Admissions")]
    assert got("Ann Lee joined Nevada State in 2020 as Registrar and leads records.")[0][3] == "Registrar"
    assert got("Nevada State University is the fastest growing university in Nevada.") == []
