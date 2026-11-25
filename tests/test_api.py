import pytest

from app import create_app, parse_list
from tests.mock_redfish import Running, make_pdu


@pytest.fixture(scope="module")
def pdus():
    a = Running(make_pdu(42))
    b = Running(make_pdu(10, expand=True, model="PDU-SWI 7979310"))
    yield a, b
    a.stop()
    b.stop()


@pytest.fixture()
def client(tmp_path, pdus, monkeypatch):
    monkeypatch.setenv("PDU_USER", "apiuser")
    monkeypatch.setenv("PDU_PASSWORD", "mock-secret-123")
    a, b = pdus
    conf = parse_list(f"Labor/Labor 1={a.url}; Prestaging/Prestaging 1={b.url}; Labor/Tot=http://127.0.0.1:1")
    app = create_app(str(tmp_path), pdus=conf, secure_cookies=False)
    c = app.test_client()
    c.post("/api/auth/setup", json={"name": "daniel", "password": "geheim12345"})
    return c


def test_parse_list():
    l = parse_list("Labor/PDU 1=10.1.0.21; Labor/PDU 2=10.1.0.22\nPrestaging 1=10.10.0.31,  ")
    assert [x["key"] for x in l] == ["pdu-1", "pdu-2", "prestaging-1"]
    assert l[0]["group"] == "Labor" and l[2]["group"] == "" and l[2]["host"] == "10.10.0.31"


def test_requires_login(tmp_path):
    app = create_app(str(tmp_path), pdus=[], secure_cookies=False)
    assert app.test_client().get("/api/pdus").status_code == 401


def test_list_and_snapshot(client, pdus):
    r = client.get("/api/pdus").get_json()
    assert [p["key"] for p in r["pdus"]] == ["labor-1", "prestaging-1", "tot"] and r["control"] is True
    s = client.get("/api/pdus/labor-1").get_json()
    assert s["info"]["model"] == "PDU-SWI 7979340" and s["info"]["firmware"] == "10.2.1"
    assert len(s["outlets"]) == 42 and s["outlets"][0]["label"] == "OT Teststack1"
    assert s["outlets"][0]["type"] == "C13" and s["outlets"][0]["phase"] == "L1" and s["outlets"][41]["phase"] == "L3"
    assert [p["name"] for p in s["phases"]] == ["L1", "L2", "L3"]
    assert s["phases"][1]["voltage"] == 224.1 and s["totals"]["frequency"] == 50.0 and s["totals"]["neutral"] == 1.02
    assert s["totals"]["energy"] == 282.0 and [b["name"] for b in s["branches"]] == ["Fuse F1", "Fuse F2"]
    s2 = client.get("/api/pdus/prestaging-1").get_json()  # mit $expand
    assert len(s2["outlets"]) == 10 and s2["outlets"][3]["type"] == "C19"


def test_offline_pdu(client):
    r = client.get("/api/pdus/tot")
    assert r.status_code == 502 and "nicht erreichbar" in r.get_json()["error"]
    s = client.get("/api/pdus/summary").get_json()
    assert s["tot"]["ok"] is False and s["labor-1"]["ok"] is True and s["labor-1"]["power"] is not None


def test_switch_and_log(client, pdus):
    a, _ = pdus
    client.get("/api/pdus/labor-1")
    r = client.post("/api/pdus/labor-1/outlets/5/power", json={"action": "Off"})
    assert r.status_code == 200 and a.app.outlets["5"]["on"] is False
    assert client.get("/api/pdus/labor-1").get_json()["outlets"][4]["state"] == "Off"  # Cache wurde verworfen
    r = client.post("/api/pdus/labor-1/power", json={"ids": ["5", "6", "99"], "action": "On"}).get_json()
    assert r["results"]["5"]["ok"] and not r["results"]["99"]["ok"] and r["ok"] is False
    assert client.post("/api/pdus/labor-1/outlets/5/power", json={"action": "Boom"}).status_code == 400
    log = client.get("/api/log?pdu=labor-1").get_json()
    assert log[0]["outlet"] == "99" and log[0]["ok"] is False and log[-1]["action"] == "Off" and log[-1]["user"] == "daniel"


def test_label(client, pdus):
    a, _ = pdus
    assert client.put("/api/pdus/labor-1/outlets/4", json={"label": "Core-SW 1"}).status_code == 200
    assert a.app.outlets["4"]["label"] == "Core-SW 1"
    assert client.put("/api/pdus/labor-1/outlets/4", json={"label": "x" * 80}).status_code == 400


def test_readonly_user(client):
    client.post("/api/admin/users", json={"name": "azubi", "password": "azubi12345", "control": False})
    client.post("/api/auth/logout", json={})
    client.post("/api/auth/login", json={"name": "azubi", "password": "azubi12345"})
    assert client.get("/api/pdus").get_json()["control"] is False
    assert client.get("/api/pdus/labor-1").status_code == 200
    assert client.post("/api/pdus/labor-1/outlets/5/power", json={"action": "Off"}).status_code == 403
    assert client.get("/api/admin/redfish/labor-1").status_code == 403


def test_raw_explorer(client):
    r = client.get("/api/admin/redfish/labor-1?path=/redfish/v1/PowerEquipment/RackPDUs/1")
    assert r.status_code == 200 and r.get_json()["Model"] == "PDU-SWI 7979340"
    assert client.get("/api/admin/redfish/labor-1?path=/etc/passwd").status_code == 400


def test_session_reused(client, pdus):
    a, _ = pdus
    client.get("/api/pdus/labor-1?force=1")
    n = a.app.state["calls"]
    client.get("/api/pdus/labor-1")  # aus dem Zwischenspeicher
    assert a.app.state["calls"] == n


def test_json_required(client):
    assert client.post("/api/pdus/labor-1/outlets/5/power", data="action=Off").status_code == 415


def test_basic_fallback(tmp_path, monkeypatch):
    m = Running(make_pdu(10, sessions=False))
    try:
        monkeypatch.setenv("PDU_USER", "apiuser")
        monkeypatch.setenv("PDU_PASSWORD", "mock-secret-123")
        app = create_app(str(tmp_path), pdus=parse_list(f"A={m.url}"), secure_cookies=False)
        c = app.test_client()
        c.post("/api/auth/setup", json={"name": "daniel", "password": "geheim12345"})
        r = c.get("/api/pdus/a")
        assert r.status_code == 200 and len(r.get_json()["outlets"]) == 10
        assert app.clients["a"].auth_mode == "Basic"
        assert c.post("/api/pdus/a/outlets/2/power", json={"action": "Off"}).status_code == 200
        monkeypatch.setenv("PDU_PASSWORD", "falsch")
        app2 = create_app(str(tmp_path), pdus=parse_list(f"A={m.url}"), secure_cookies=False)
        c2 = app2.test_client()
        c2.post("/api/auth/login", json={"name": "daniel", "password": "geheim12345"})
        r = c2.get("/api/pdus/a")
        assert r.status_code == 502 and "Session: PDU meldet 401" in r.get_json()["error"] and "Basic: PDU meldet 401" in r.get_json()["error"]
        n = m.app.state["calls"]
        r = c2.get("/api/pdus/a?force=1")  # gesperrt: kein neuer Anmeldeversuch an der PDU
        assert r.status_code == 502 and "nächster Versuch" in r.get_json()["error"] and m.app.state["calls"] == n
    finally:
        m.stop()
