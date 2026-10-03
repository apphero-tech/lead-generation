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
