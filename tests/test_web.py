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
    m = canonical_cities(["Fort Lauderdale"] * 6 + ["Ft Laurderdale", "St. Petersburg", "Saint Petersburg", "Saint Petersburg"])
    assert m["Ft Laurderdale"] == "Fort Lauderdale" and m["St. Petersburg"] == "Saint Petersburg"
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
