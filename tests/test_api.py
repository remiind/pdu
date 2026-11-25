import pytest

from app import create_app, parse_list
from tests.mock_redfish import Running, make_pdu, make_rittal


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


def test_diag(client):
    r = client.get("/api/admin/diag/labor-1")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    d = r.get_json()
    assert d["auth_mode"] == "Session" and "/redfish/v1/PowerEquipment/RackPDUs/1/Outlets/1" in d["data"]
    assert "mock-secret" not in r.get_data(as_text=True)


def test_rittal_real(tmp_path, monkeypatch):
    """Verhalten der echten Rittal-PDU (Diagnose-Paket 25.11.2026)."""
    import app.redfish as rf
    monkeypatch.setattr(rf, "CYCLE_PAUSE", 0.05)
    m = Running(make_rittal(42))
    try:
        monkeypatch.setenv("PDU_USER", "apiuser")
        monkeypatch.setenv("PDU_PASSWORD", "mock-secret-123")
        app = create_app(str(tmp_path), pdus=parse_list(f"Labor/R={m.url}"), secure_cookies=False)
        c = app.test_client()
        c.post("/api/auth/setup", json={"name": "daniel", "password": "geheim12345"})
        s = c.get("/api/pdus/r").get_json()
        o = s["outlets"]
        assert [x["num"] for x in o] == list(range(1, 43)) and o[0]["id"] == "L1_1" and o[33]["id"] == "L3_34"
        assert s["counts"]["on"] == 42 and o[0]["type"] == "C13" and o[0]["label"] == "OT Teststack1" and o[41]["phase"] == "L3"
        assert o[0]["current"] is None and s["metering"] is False  # 0,0 A je Socket = nicht gemessen
        assert [p["name"] for p in s["phases"]] == ["L1", "L2", "L3"]
        assert s["phases"][2]["current"] == 1.61 and s["phases"][0]["voltage"] == 227.8 and s["phases"][1]["power"] == 133.0
        assert s["phases"][0]["energy"] == 17.4 and round(s["totals"]["energy"], 1) == 275.0  # Summe der Phasen, nicht 10.194.415
        assert s["totals"]["frequency"] == 50.0 and s["totals"]["power"] == 467.0
        assert s["info"]["model"] == "7979340 PDU-SWI" and s["info"]["location"] is None
        # Neustart einzeln: Aus + An
        assert c.post("/api/pdus/r/outlets/L1_5/power", json={"action": "PowerCycle"}).status_code == 200
        assert m.app.state["actions"][-2:] == [("L1_5", "Off"), ("L1_5", "On")]
        # Neustart als Auswahl: erst alle aus, dann alle an
        r = c.post("/api/pdus/r/power", json={"ids": ["L1_6", "L1_7"], "action": "PowerCycle"}).get_json()
        assert r["ok"] and m.app.state["actions"][-4:] == [("L1_6", "Off"), ("L1_7", "Off"), ("L1_6", "On"), ("L1_7", "On")]
        assert c.post("/api/pdus/r/outlets/L1_8/power", json={"action": "Off"}).status_code == 200
        assert c.get("/api/pdus/r").get_json()["counts"]["off"] == 1
        # Umbenennen: Redfish lehnt UserLabel ab → Weboberfläche (json.cgi, DescName)
        r = c.put("/api/pdus/r/outlets/L1_2", json={"label": "Core-SW 1"})
        assert r.status_code == 200 and r.get_json()["label"] == "Core-SW 1"
        assert m.app.web["sets"][-1] == (139, "Core-SW 1")  # Socket 02 → Variable 139 wie auf der echten PDU
        assert c.get("/api/pdus/r").get_json()["outlets"][1]["label"] == "Core-SW 1"
        logins = m.app.web["logins"]
        assert c.put("/api/pdus/r/outlets/L1_3", json={"label": "AP_Lab-3"}).status_code == 200
        assert m.app.web["logins"] == logins  # Sitzung wird wiederverwendet
        bad = c.put("/api/pdus/r/outlets/L1_3", json={"label": "Prüfstand/1"})
        assert bad.status_code == 400 and "höchstens 20 Zeichen" in bad.get_json()["error"]
        assert c.put("/api/pdus/r/outlets/L1_3", json={"label": "x" * 21}).status_code == 400
        assert c.put("/api/pdus/r/outlets/L1_2", json={"label": ""}).get_json()["label"] == "Socket 02"  # leer = Standardname
        m.app.web["sessions"].clear()  # Sitzung abgelaufen → neu anmelden
        assert c.put("/api/pdus/r/outlets/L1_2", json={"label": "Neu"}).status_code == 200
    finally:
        m.stop()


def test_groups(client):
    client.get("/api/pdus/labor-1")
    assert client.put("/api/pdus/labor-1/groups", json={"ids": ["1", "2"], "group": "  Teststack   1 "}).get_json()["group"] == "Teststack 1"
    s = client.get("/api/pdus/labor-1").get_json()
    assert [o["group"] for o in s["outlets"][:3]] == ["Teststack 1", "Teststack 1", ""] and s["groups"] == ["Teststack 1"]
    assert client.put("/api/pdus/labor-1/groups", json={"ids": ["2"], "group": ""}).status_code == 200
    assert client.get("/api/pdus/labor-1").get_json()["outlets"][1]["group"] == ""
    assert client.put("/api/pdus/labor-1/groups", json={"ids": ["99"], "group": "x"}).status_code == 404
    assert client.put("/api/pdus/labor-1/groups", json={"ids": ["1"], "group": "x" * 40}).status_code == 400
    assert client.get("/api/log?pdu=labor-1").get_json()[0]["action"] == "Group"
    client.put("/api/pdus/labor-1/groups", json={"ids": ["5"], "group": "Core"})
    client.put("/api/pdus/labor-1/groups", json={"ids": ["6"], "group": "Backup"})
    cols = client.get("/api/pdus/labor-1").get_json()["groupColors"]
    assert set(cols) == {"Teststack 1", "Core", "Backup"} and len(set(cols.values())) == 3  # jede Gruppe eigene Farbe
    first = cols["Teststack 1"]
    client.put("/api/pdus/labor-1/groups", json={"ids": ["7"], "group": "Aaa neu"})
    assert client.get("/api/pdus/labor-1").get_json()["groupColors"]["Teststack 1"] == first  # bleibt stabil


def test_background_and_disk_cache(tmp_path, monkeypatch, pdus):
    import time as _t
    monkeypatch.setenv("PDU_USER", "apiuser")
    monkeypatch.setenv("PDU_PASSWORD", "mock-secret-123")
    a, _ = pdus
    conf = parse_list(f"A={a.url}")
    app = create_app(str(tmp_path), pdus=conf, secure_cookies=False, background=True)
    for _ in range(50):  # Hintergrund liest die PDU ohne Aufruf
        if app.clients["a"]._snap:
            break
        _t.sleep(0.1)
    assert app.clients["a"]._snap and (tmp_path / "cache" / "a.json").exists()
    # Neustart ohne Hintergrund: gespeicherter Stand ist sofort da
    app2 = create_app(str(tmp_path), pdus=conf, secure_cookies=False, background=False)
    assert app2.clients["a"].from_disk and len(app2.clients["a"]._snap["outlets"]) == 42


def test_serves_cache_immediately(tmp_path, monkeypatch, pdus):
    """Mit Hintergrund: auch ein älterer Stand wird sofort geliefert (und neu angestoßen), statt zu warten."""
    import time as _t
    monkeypatch.setenv("PDU_USER", "apiuser")
    monkeypatch.setenv("PDU_PASSWORD", "mock-secret-123")
    a, _ = pdus
    app = create_app(str(tmp_path), pdus=parse_list(f"A={a.url}"), secure_cookies=False, background=True)
    c = app.test_client()
    c.post("/api/auth/setup", json={"name": "daniel", "password": "geheim12345"})
    for _ in range(50):
        if app.clients["a"]._snap:
            break
        _t.sleep(0.1)
    app.clients["a"]._snap = {**app.clients["a"]._snap, "ts": _t.time() - 300}  # künstlich alt
    r = c.get("/api/pdus/a").get_json()
    assert r["age"] >= 299  # sofort aus dem Speicher, nicht direkt gelesen


def test_partial_failures_keep_sockets(monkeypatch):
    """Ein einzelner Socket-Ausfall nimmt den letzten Wert; fällt die Liste aus, bleibt der letzte gute Stand."""
    from app.redfish import PduClient, RedfishError
    m = Running(make_rittal(42))
    try:
        c = PduClient("r", "R", m.url, "apiuser", "mock-secret-123")
        assert len(c.snapshot(force=True)["outlets"]) == 42
        m.app.state["fail"] = {"L1_5", "L2_20"}
        s = c.snapshot(force=True)
        assert len(s["outlets"]) == 42 and s["outlets"][4]["id"] == "L1_5"
        m.app.state["fail_collection"] = True
        with pytest.raises(RedfishError):
            c.snapshot(force=True)
        assert len(c._snap["outlets"]) == 42  # guter Stand bleibt
    finally:
        m.stop()


def test_orientation_setting(client):
    assert client.get("/api/pdus/labor-1").get_json()["settings"]["orient"] == "desc"
    assert client.put("/api/pdus/labor-1/settings", json={"orient": "asc"}).get_json()["settings"]["orient"] == "asc"
    assert client.get("/api/pdus/labor-1").get_json()["settings"]["orient"] == "asc"
    assert client.get("/api/pdus/prestaging-1").get_json()["settings"]["orient"] == "desc"
    assert client.put("/api/pdus/labor-1/settings", json={"orient": "quer"}).status_code == 400
