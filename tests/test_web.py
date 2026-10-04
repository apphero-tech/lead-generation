import base64

from leadgen.config import Settings
from leadgen.web import create_app


def client(tmp_path, password=""):
    s = Settings(db_path=tmp_path / "t.db", out_dir=tmp_path / "out", ui_password=password)
    return create_app(s).test_client()


def test_open_without_password(tmp_path):
    assert client(tmp_path).get("/api/states").status_code == 200


def test_password_required_when_set(tmp_path):
    c = client(tmp_path, "s3cret")
    assert c.get("/").status_code == 401
    good = base64.b64encode(b"apphero:s3cret").decode()
    bad = base64.b64encode(b"apphero:nope").decode()
    assert c.get("/api/states", headers={"Authorization": "Basic " + good}).status_code == 200
    assert c.get("/api/states", headers={"Authorization": "Basic " + bad}).status_code == 401


def test_city_variants_and_radius(tmp_path):
    from leadgen.db import connect
    from leadgen.ipeds import canonical_cities
    from leadgen.web import select_institutions
    fl = (26.12, -80.14)
    m = canonical_cities([("Fort Lauderdale",) + fl + ("Keiser University-Ft Lauderdale",)] * 6 + [("Ft Laurderdale", 26.11, -80.15),
                         ("St. Petersburg", 27.77, -82.64), ("Saint Petersburg", 27.77, -82.64)])
    assert m["Ft Laurderdale"] == "Fort Lauderdale" and m["St. Petersburg"] == "Saint Petersburg"
    # Look-alike names of distinct, distant towns stay separate; suburbs stay separate.
    m = canonical_cities([("Santa Clara", 37.35, -121.95), ("Santa Clarita", 34.39, -118.54),
                          ("Charleston", 38.35, -81.63), ("Charles Town", 39.29, -77.86),
                          ("Hartford", 41.76, -72.67), ("West Hartford", 41.76, -72.74),
                          ("N Little Rock", 34.77, -92.27), ("North Little Rock", 34.78, -92.26),
                          ("Washing", 38.55, -91.01, "Evolve Beauty Academy"),
                          ("Washington", 38.55, -91.02, "Washington Career Center"),
                          ("Aguada", 18.38, -67.19, "Colegio A"), ("Aguada", 18.38, -67.19, "Colegio B"),
                          ("Aguadilla", 18.43, -67.15, "Inter American University-Aguadilla"),
                          ("San Angelon", 31.44, -100.45, "Texas College of Cosmetology-San Angelo"),
                          ("San Angelo", 31.44, -100.46, "Angelo State University")])
    assert m["Santa Clarita"] == "Santa Clarita" and m["Charles Town"] == "Charles Town"
    assert m["West Hartford"] == "West Hartford"
    assert m["N Little Rock"] == m["North Little Rock"] and m["Washing"] == "Washington"
    assert m["Aguada"] == "Aguada" and m["San Angelon"] == "San Angelo"
    conn = connect(tmp_path / "t.db")
    rows = [("1", "A", "Fort Lauderdale", "26.12", "-80.14"), ("2", "B", "Lauderhill", "26.16", "-80.21"),
            ("3", "C", "Miami", "25.77", "-80.19")]
    for uid, name, city, lat, lon in rows:
        conn.execute("INSERT INTO institutions (unitid, state, name, city, latitude, longitude, is_system) "
                     "VALUES (?, 'FL', ?, ?, ?, ?, 0)", (uid, name, city, lat, lon))
    names = lambda r: sorted(i["name"] for i in select_institutions(conn, "FL", "Fort Lauderdale", radius_km=r))
    assert names(0) == ["A"]
    assert names(15) == ["A", "B"]      # Lauderhill is ~8 km away
    assert names(50) == ["A", "B", "C"]  # Miami is ~40 km away


def test_delete_generated_file(tmp_path):
    c = client(tmp_path)
    f = tmp_path / "out" / "FL" / "contacts_FL_x.xlsx"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert c.delete("/files/FL/contacts_FL_x.xlsx").status_code == 200
    assert not f.exists()
    assert c.delete("/files/../t.db").status_code == 404  # never outside the output folder
