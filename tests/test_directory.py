from leadgen.directory import analyse_form, parse_results
from leadgen.pipeline import unit_from_title

FORM = """<form action="/directory/SearchPerson" method="post">
<input type="text" name="firstName"><input type="text" name="lastName"><input type="text" name="email">
<input type="radio" name="auth" value="false" checked><input type="radio" name="auth" value="true"></form>"""

SITE_SEARCH = """<form action="https://www.google.com/cse" method="get"><input name="q"></form>"""

RESULTS = """<div class="list-card"><a href="/x">Doe, Jane</a><label>Phone:</label><a>555-294-0964</a>
<label>Email:</label><a href="mailto:janedoe@u.edu">janedoe@u.edu</a></div>
<div class="list-card"><a href="/y">Doe, Tom</a><label>Email:</label><a>tdoe@u.edu</a></div>"""


def test_first_last_form_detected():
    f = analyse_form(FORM, "https://directory.u.edu/")
    assert f.action == "https://directory.u.edu/directory/SearchPerson" and f.method == "post"
    assert f.params("Jane", "Doe") == {"firstName": "Jane", "lastName": "Doe", "email": "", "auth": "false"}


def test_site_search_box_is_not_a_directory():
    assert analyse_form(SITE_SEARCH, "https://www.u.edu/") is None


def test_results_take_only_the_named_person():
    hit = parse_results(RESULTS, "Jane", "Doe")
    assert hit.email == "janedoe@u.edu" and hit.phone == "(555) 294-0964" and hit.entries == 1
    assert parse_results(RESULTS, "Anna", "Smith").email == ""


def test_unit_from_page_title():
    assert unit_from_title("Advancement Staff | College of Engineering", "eng.u.edu") \
        == "College of Engineering"
    assert unit_from_title("Home", "it.u.edu") == "it.u.edu"
