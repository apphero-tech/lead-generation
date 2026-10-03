from leadgen.email_pattern import deduce, detect_pattern, email_matches_name, patterns_for


def test_patterns_for():
    assert "first.last" in patterns_for("Jane", "Doe", "jane.doe")
    assert "flast" in patterns_for("Jane", "Doe", "jdoe2")
    assert patterns_for("Rob", "Allen", "rob") == []


def test_detect_requires_majority_and_examples():
    pairs = [("Ann", "Lee", "ann.lee@x.edu"), ("Bob", "Ray", "bob.ray@x.edu"), ("Cy", "Moe", "cy.moe@x.edu")]
    assert detect_pattern(pairs, 3, 0.6)[0] == "first.last"
    noisy = pairs + [("Dan", "Fox", "zz91@x.edu"), ("Eve", "Kim", "qq7@x.edu"), ("Gus", "Orr", "t1@x.edu")]
    assert detect_pattern(noisy, 3, 0.6) is None  # only 50% fit: no deduction
    assert detect_pattern(pairs[:2], 3, 0.6) is None  # too few examples


def test_deduce_strips_accents():
    assert deduce("first.last", "José", "Núñez", "x.edu") == "jose.nunez@x.edu"


def test_email_matches_name_rejects_same_last_name_other_person():
    assert email_matches_name("rita.stone@u.edu", "Rita", "Stone")
    assert not email_matches_name("mona.stone@u.edu", "Rita", "Stone")


def test_name_in_email_loose():
    from leadgen.email_pattern import name_in_email
    assert name_in_email("sfig@u.edu", "Steve", "Figaro")
    assert name_in_email("natkara@u.edu", "Natalia", "Karadag")
    assert not name_in_email("dshores@u.edu", "Dana", "Kennedy")
