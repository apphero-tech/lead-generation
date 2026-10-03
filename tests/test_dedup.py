from leadgen.extract import Candidate
from leadgen.pipeline import merge_candidates


def cand(first, last, pid, q="exact", url="https://u.edu/a", email=""):
    return Candidate(first, last, pid, q, "title", url, email=email)


def test_same_person_two_profiles_is_one_record():
    people = merge_candidates([cand("Jane", "Doe", "adv_vp"), cand("Jane", "Doe", "fdn_ed")], 5)
    assert len(people) == 1
    assert set(people[0]["roles"]) == {"adv_vp", "fdn_ed"}


def test_accents_and_middle_names_merge():
    people = merge_candidates([cand("José", "Núñez", "cio"), cand("Jose", "Nunez", "cio", url="https://u.edu/b")], 5)
    assert len(people) == 1
    assert len(people[0]["roles"]["cio"]["sources"]) == 2


def test_cap_per_profile_prefers_exact():
    cands = [cand("A", "One", "dev_dir", "close"), cand("Bea", "Two", "dev_dir"), cand("Cal", "Three", "dev_dir")]
    people = merge_candidates(cands, 2)
    kept = {p["last_name"] for p in people}
    assert kept == {"Two", "Three"}


def test_published_email_kept():
    people = merge_candidates([cand("Jane", "Doe", "cio"), cand("Jane", "Doe", "cio", email="jane.doe@u.edu")], 5)
    assert people[0]["emails"][0][0] == "jane.doe@u.edu"
