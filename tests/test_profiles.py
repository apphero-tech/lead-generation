from leadgen.profiles import match_title


def ids(title):
    return {(p.id, q) for p, q in match_title(title)}


def test_vp_enrollment_not_associate():
    assert ("enr_vp", "exact") in ids("Vice President for Enrollment Management")
    got = ids("Associate Vice President, Enrollment Management")
    assert ("enr_avp", "exact") in got and not any(i == "enr_vp" for i, _ in got)


def test_cio_variants():
    assert ("cio", "exact") in ids("Senior Vice President and CIO, IT")
    assert ("cio", "exact") in ids("VP of Information Technology")
    assert not any(i == "cio" and q == "exact" for i, q in ids("Deputy CIO"))


def test_support_staff_are_not_leaders():
    assert ids("Executive Assistant to the Vice President for Advancement") == set()
    assert ids("Chief of Staff and Executive Assistant to the Vice President") == set()


def test_development_excludes_other_meanings():
    assert ("dev_dir", "exact") in ids("Senior Director of Development, College of Engineering")
    assert not any(i == "dev_dir" for i, _ in ids("Director of Professional Development"))
    assert not any(i == "dev_dir" for i, _ in ids("Director of Software Development"))


def test_registrar_and_foundation():
    assert ("registrar", "exact") in ids("University Registrar")
    assert ("registrar", "close") in ids("Associate Registrar")
    assert ("fdn_ed", "exact") in ids("President and CEO, University of Florida Foundation")
    assert not any(i == "fdn_ed" for i, _ in ids("Vice President, UF Foundation"))


def test_one_title_can_cover_two_profiles():
    got = {i for i, _ in ids("Vice President for Advancement and Executive Director of the Foundation")}
    assert {"adv_vp", "fdn_ed"} <= got


def test_workforce_and_continuing_ed():
    assert ("wf_dir", "exact") in ids("Dean of Workforce Education")
    assert ("ce_dir", "exact") in ids("Director, Professional and Continuing Education")


def test_sentences_and_outside_jobs_rejected():
    assert ids("The Office of the University Registrar will be closed on Monday") == set()
    assert not any(i == "adv_vp" for i, _ in ids("VP New Business Development, Applied Materials"))
    assert not any(i == "fdn_ed" for i, _ in ids("Executive Director of Development, Corporate & Foundation Relations"))


def test_headings_and_descriptions_rejected():
    assert ids("CIO Senior Leadership") == set()
    assert ids("Support staff development activities in the Office of the University Registrar") == set()
    assert ("adv_cao", "exact") in ids("Associate Vice President/Chief Advancement Officer")
    assert ("alu_dir", "exact") in ids("Senior Director of Alumni Engagement")


def test_it_and_crm_titles():
    got = ids("Director, Applications, Development, and Integrations")
    assert not any(i == "dev_dir" for i, _ in got) and ("is_dir", "close") in got
    assert ("crm_dir", "close") in ids("CRM Salesforce Developer")
    assert not any(i == "dev_dir" for i, _ in ids("Senior Director of Development Communications"))


def test_former_titles_rejected():
    assert ids("Former Chief Information Officer of AstraZeneca") == set()
