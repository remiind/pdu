"""Simulierte Rittal-PDU (Redfish) für Tests und lokale Entwicklung.

    python tests/mock_redfish.py 9101 42     # PDU mit 42 Sockets auf http://127.0.0.1:9101
"""
import json
import random
import secrets
import sys
import threading
import time

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

USER, PASSWORD = "apiuser", "mock-secret-123"


def make_pdu(n_outlets=42, expand=False, model="PDU-SWI 7979340", sessions=True):
    app = Flask("mock-redfish")
    tokens = set()
    P = "/redfish/v1/PowerEquipment/RackPDUs/1"
    phases = ["Line1", "Line2", "Line3"]
    per = -(-n_outlets // 3)
    outlets = {}
    for i in range(1, n_outlets + 1):
        ph = phases[min(2, (i - 1) // per)]
        outlets[str(i)] = {
            "label": {1: "OT Teststack1", 2: "OT Teststack2", 3: "OT Testswitch1"}.get(i, ""),
            "type": "IEC_60320_C19" if (i - 1) % per >= per - 2 else "IEC_60320_C13",
            "phase": ph, "on": i % 7 != 0, "load": random.choice([0, 0, 0.12, 0.31, 0.55, 0.9]),
        }
    state = {"calls": 0, "actions": []}

    def auth_ok():
        if request.headers.get("X-Auth-Token") in tokens:
            return True
        a = request.authorization
        return bool(a and a.username == USER and a.password == PASSWORD)

    @app.before_request
    def check():
        state["calls"] += 1
        if request.path in ("/redfish", "/redfish/v1", "/redfish/v1/") or (request.path == "/redfish/v1/SessionService/Sessions" and request.method == "POST"):
            return None
        if not auth_ok():
            return jsonify(error={"code": "Base.1.8.NoValidSession", "message": "No valid session"}), 401
        return None

    @app.post("/redfish/v1/SessionService/Sessions")
    def login():
        if not sessions:  # wie eine PDU, die keine Sessions für diesen Benutzer erlaubt
            return jsonify(error={"code": "Base.1.8.InsufficientPrivilege", "message": "Session creation not permitted"}), 401
        b = request.get_json(force=True)
        if b.get("UserName") != USER or b.get("Password") != PASSWORD:
            return jsonify(error={"code": "Base.1.8.NoValidSession", "message": "Invalid credentials"}), 401
        t = secrets.token_hex(8)
        tokens.add(t)
        r = jsonify({"Id": t[:4], "UserName": USER})
        r.headers["X-Auth-Token"] = t
        r.headers["Location"] = "/redfish/v1/SessionService/Sessions/" + t[:4]
        return r, 201

    @app.get("/redfish/v1")
    def root():
        return jsonify({"Id": "RootService", "PowerEquipment": {"@odata.id": "/redfish/v1/PowerEquipment"},
                        "ProtocolFeaturesSupported": {"ExpandQuery": {"Levels": expand, "MaxLevels": 1}}})

    def outlet_json(oid):
        o = outlets[oid]
        cur = round(o["load"] * (1 if o["on"] else 0), 2)
        return {
            "@odata.id": f"{P}/Outlets/{oid}", "Id": oid, "Name": f"Socket {int(oid):02d}", "UserLabel": o["label"],
            "OutletType": o["type"], "PowerState": "On" if o["on"] else "Off", "ElectricalContext": o["phase"],
            "PhaseWiringType": "OnePhase3Wire", "RatedCurrentAmps": 10 if "C13" in o["type"] else 16,
            "CurrentAmps": {"Reading": cur}, "PowerWatts": {"Reading": round(cur * 229)},
            "EnergykWh": {"Reading": round(int(oid) * 1.7, 1)}, "Voltage": {"Reading": 229.1},
            "Status": {"State": "Enabled", "Health": "OK"},
            "Links": {"BranchCircuit": {"@odata.id": f"{P}/Branches/{'F1' if o['phase'] != 'Line3' else 'F2'}"}},
            "Actions": {"#Outlet.PowerControl": {"target": f"{P}/Outlets/{oid}/Actions/Outlet.PowerControl"}},
        }

    def col(path, ids, fn):
        if expand and "$expand" in request.query_string.decode():
            return jsonify({"Members": [fn(i) for i in ids], "Members@odata.count": len(ids)})
        return jsonify({"Members": [{"@odata.id": f"{path}/{i}"} for i in ids], "Members@odata.count": len(ids)})

    @app.get("/redfish/v1/PowerEquipment/RackPDUs")
    def pdus():
        return jsonify({"Members": [{"@odata.id": P}]})

    @app.get(P)
    def pdu():
        return jsonify({"@odata.id": P, "Id": "1", "Name": "PDU-SWI", "Model": model, "Manufacturer": "Rittal GmbH & Co. KG",
                        "SerialNumber": "7979340-0042", "FirmwareVersion": "10.2.1", "EquipmentType": "RackPDU",
                        "Location": {"Placement": {"Rack": "Labor R3"}}, "Status": {"State": "Enabled", "Health": "OK"},
                        "Mains": {"@odata.id": P + "/Mains"}, "Branches": {"@odata.id": P + "/Branches"},
                        "Outlets": {"@odata.id": P + "/Outlets"}, "Metrics": {"@odata.id": P + "/Metrics"}})

    def phase_sum(line):
        return sum(o["load"] for o in outlets.values() if o["on"] and o["phase"] == line)

    def mains_json(_i="AC1"):
        cur = {f"Line{k}": {"Reading": round(phase_sum(f'Line{k}'), 2)} for k in (1, 2, 3)}
        return {"@odata.id": P + "/Mains/AC1", "Id": "AC1", "Name": "Input", "FrequencyHz": {"Reading": 50.0},
                "PhaseWiringType": "ThreePhase5Wire", "RatedCurrentAmps": 16,
                "PolyPhaseVoltage": {"Line1ToNeutral": {"Reading": 218.8}, "Line2ToNeutral": {"Reading": 224.1}, "Line3ToNeutral": {"Reading": 219.8}},
                "PolyPhaseCurrentAmps": {**cur, "Neutral": {"Reading": 1.02}},
                "PolyPhasePowerWatts": {f"Line{k}ToNeutral": {"Reading": round(phase_sum(f'Line{k}') * 220)} for k in (1, 2, 3)},
                "PolyPhaseEnergykWh": {"Line1ToNeutral": {"Reading": 17.4}, "Line2ToNeutral": {"Reading": 22.4}, "Line3ToNeutral": {"Reading": 242.2}},
                "Status": {"State": "Enabled", "Health": "OK"}}

    @app.get(P + "/Mains")
    def mains():
        return col(P + "/Mains", ["AC1"], mains_json)

    @app.get(P + "/Mains/<i>")
    def main1(i):
        return jsonify(mains_json(i))

    def branch_json(i):
        return {"@odata.id": f"{P}/Branches/{i}", "Id": i, "Name": f"Fuse {i}", "BreakerState": "Normal", "RatedCurrentAmps": 16,
                "CurrentAmps": {"Reading": 1.3 if i == "F1" else 0.7}, "Status": {"State": "Enabled", "Health": "OK"}}

    @app.get(P + "/Branches")
    def branches():
        return col(P + "/Branches", ["F1", "F2"], branch_json)

    @app.get(P + "/Branches/<i>")
    def branch(i):
        return jsonify(branch_json(i))

    @app.get(P + "/Metrics")
    def metrics():
        return jsonify({"PowerWatts": {"Reading": round(sum(phase_sum(f'Line{k}') for k in (1, 2, 3)) * 220)}, "EnergykWh": {"Reading": 282.0}})

    @app.get(P + "/Outlets")
    def outl():
        return col(P + "/Outlets", list(outlets), outlet_json)

    @app.get(P + "/Outlets/<oid>")
    def outlet(oid):
        if oid not in outlets:
            return jsonify(error={"code": "Base.1.8.ResourceMissingAtURI", "message": "not found"}), 404
        return jsonify(outlet_json(oid))

    @app.patch(P + "/Outlets/<oid>")
    def patch(oid):
        b = request.get_json(force=True)
        if "UserLabel" in b:
            outlets[oid]["label"] = b["UserLabel"]
        return "", 204

    @app.post(P + "/Outlets/<oid>/Actions/Outlet.PowerControl")
    def power(oid):
        ps = request.get_json(force=True).get("PowerState")
        if ps not in ("On", "Off", "PowerCycle"):
            return jsonify(error={"code": "Base.1.8.ActionParameterValueNotInList", "message": f"Value {ps} not allowed"}), 400
        state["actions"].append((oid, ps))
        outlets[oid]["on"] = ps != "Off"
        return "", 204

    app.state = state
    app.outlets = outlets
    return app


def make_rittal(n_outlets=42, sid="43090021", delay=0.0):
    """Nachbau des echten Verhaltens einer Rittal PDU-SWI 7979340 (FW 10.2.1), siehe Diagnose-Paket:
    Outlet-IDs „L3_34“, Readings als Excerpt mit DataSourceUri, kein Strom je Socket (0,0 A),
    PowerControl nur On/Off, $expand liefert gekürzte Mitglieder, Energie je Phase nur als Chassis-Sensor,
    Metrics.EnergykWh in falscher Einheit, RackPDUs/<id>/Sensors → 405."""
    app = Flask("mock-rittal")
    tokens = set()
    P = f"/redfish/v1/PowerEquipment/RackPDUs/{sid}"
    C = f"/redfish/v1/Chassis/PDU_{sid}"
    per = -(-n_outlets // 3)
    outlets = {}
    for i in range(1, n_outlets + 1):
        ph = min(3, (i - 1) // per + 1)
        outlets[f"L{ph}_{i}"] = {"num": i, "phase": ph, "on": True, "label": f"Socket {i}" if i > 3 else f"OT Teststack{i}",
                                 "type": "IEC_60320_C19" if (i - 1) % per >= per - 2 else "IEC_60320_C13"}
    state = {"calls": 0, "actions": []}
    ex = lambda u, v: {"DataSourceUri": u, "Reading": v}  # noqa: E731
    cur = {1: 0.32, 2: 0.66, 3: 1.61}
    pw = {1: 43.0, 2: 133.0, 3: 291.0}
    vol = {1: 227.8, 2: 226.7, 3: 227.1}
    en = {1: 17.4, 2: 22.4, 3: 235.2}

    slow = threading.Lock()

    @app.before_request
    def check():
        state["calls"] += 1
        if delay:  # wie die echte PDU: ~0,07 s je Anfrage, eine nach der anderen
            with slow:
                time.sleep(delay)
        if (request.path == "/redfish/v1/SessionService/Sessions" and request.method == "POST") or request.path.startswith("/cgi-bin/"):
            return None
        if request.headers.get("X-Auth-Token") in tokens:
            return None
        return jsonify(error={"code": "Base.1.21.Unauthorized", "message": "Authentication is required and has failed or has not been provided"}), 401

    @app.post("/redfish/v1/SessionService/Sessions")
    def login():
        b = request.get_json(force=True)
        if b.get("UserName") != USER or b.get("Password") != PASSWORD:
            return jsonify(error={"code": "Base.1.21.Unauthorized", "message": "Authentication is required and has failed or has not been provided"}), 401
        t = secrets.token_hex(8)
        tokens.add(t)
        r = jsonify({"Id": t[:4]})
        r.headers["X-Auth-Token"] = t
        r.headers["Location"] = "/redfish/v1/SessionService/Sessions/" + t[:4]
        return r, 201

    def outlet_json(oid, short=False):
        o = outlets[oid]
        d = {"@odata.id": f"{P}/Outlets/{oid}", "Id": oid, "Name": f"Outlet {oid}", "ElectricalContext": f"Line{o['phase']}ToNeutral",
             "NominalVoltage": "AC230V", "PhaseWiringType": "OnePhase3Wire", "RatedCurrentAmps": 0.0, "Status": {"Health": "OK", "State": "Enabled"},
             "CurrentAmps": {}, "Links": {"BranchCircuit": {}}}
        if short:
            return d
        d.update({"CurrentAmps": ex(f"{C}/Sensors/Current_L{o['phase']}_{o['num']}", 0.0), "OutletType": o["type"], "PowerState": "On" if o["on"] else "Off",
                  "PowerEnabled": True, "UserLabel": o["label"], "ElectricalConsumerNames": o.get("consumers", []), "Links": {"BranchCircuit": {"@odata.id": f"{P}/Branches/L{o['phase']}"}},
                  "Actions": {"#Outlet.PowerControl": {"PowerState@Redfish.AllowableValues": ["On", "Off"], "target": f"{P}/Outlets/{oid}/Actions/Outlet.PowerControl"}}})
        return d

    @app.get("/redfish/v1")
    def root():
        return jsonify({"@odata.id": "/redfish/v1", "Vendor": "Rittal GmbH & Co. KG", "PowerEquipment": {"@odata.id": "/redfish/v1/PowerEquipment"},
                        "ProtocolFeaturesSupported": {"ExpandQuery": {"ExpandAll": True, "Levels": True, "MaxLevels": 5}}})

    @app.get("/redfish/v1/PowerEquipment/RackPDUs")
    def pdus():
        return jsonify({"Members": [{"@odata.id": P}], "Members@odata.count": 1})

    @app.get(P)
    def pdu():
        return jsonify({"@odata.id": P, "Id": sid, "Name": "Rittal PDU Controller", "Description": "PDU-SWI", "Model": "PDU-MAN", "PartNumber": "7979340",
                        "SerialNumber": sid, "FirmwareVersion": "v10.2.1", "Manufacturer": "Rittal GmbH & Co. KG", "UserLabel": "PDU-MAN",
                        "Location": {"Placement": {"AdditionalInfo": "Location", "Rack": "Rack"}}, "Status": {"Health": "OK", "State": "Enabled"},
                        "Links": {"Chassis": [{"@odata.id": C}]}, "Mains": {"@odata.id": P + "/Mains"}, "Branches": {"@odata.id": P + "/Branches"},
                        "Outlets": {"@odata.id": P + "/Outlets"}, "Metrics": {"@odata.id": P + "/Metrics"}})

    @app.get(P + "/Sensors")
    def no_sensors():
        return jsonify(error={"code": "Base.1.21.OperationNotAllowed", "message": "The requested operation is not implemented."}), 405

    @app.get(P + "/Mains")
    def mains():
        return jsonify({"Members": [{"@odata.id": P + "/Mains/AC1"}]})

    @app.get(P + "/Mains/AC1")
    def ac1():
        return jsonify({"Id": "AC1", "Name": "Mains AC Input", "CircuitType": "Mains", "ElectricalContext": "Total", "NominalFrequencyHz": 0.0,
                        "EnergykWh": ex(f"{C}/Sensors/Energy_L3", en[3]), "RatedCurrentAmps": 0.0, "PowerState": "On",
                        "PolyPhaseCurrentAmps": {f"Line{k}": ex(f"{C}/Sensors/Current_L{k}", cur[k]) for k in (1, 2, 3)},
                        "PolyPhasePowerWatts": {f"Line{k}ToNeutral": ex(f"{C}/Sensors/Power_L{k}", pw[k]) for k in (1, 2, 3)},
                        "PolyPhaseVoltage": {f"Line{k}ToNeutral": ex(f"{C}/Sensors/Voltage_L{k}", vol[k]) for k in (1, 2, 3)},
                        "PowerWatts": ex(f"{C}/Sensors/Power_AC1_Total", sum(pw.values())), "Status": {"Health": "OK", "State": "Enabled"}})

    @app.get(P + "/Branches")
    def branches():
        return jsonify({"Members": [{"@odata.id": f"{P}/Branches/L{k}"} for k in (1, 2, 3)]})

    @app.get(P + "/Branches/<b>")
    def branch(b):
        k = int(b[1])
        return jsonify({"Id": b, "Name": f"Branch Circuit {b}", "CurrentAmps": ex("", cur[k]), "PowerWatts": ex("", pw[k]),
                        "PolyPhaseVoltage": {f"Line{k}ToNeutral": ex("", vol[k])}})

    @app.get(P + "/Metrics")
    def metrics():
        return jsonify({"EnergykWh": ex(f"{C}/Sensors/Energy_AC1_Total", 10194415.0), "PowerWatts": ex(f"{C}/Sensors/Power_AC1_Total", sum(pw.values()))})

    @app.get(P + "/Outlets")
    def outl():
        if state.get("fail_collection"):
            return jsonify(error={"code": "Base.1.21.GeneralError", "message": "busy"}), 503
        if "$expand" in request.query_string.decode():
            return jsonify({"Members": [outlet_json(i, short=True) for i in outlets], "Members@odata.count": len(outlets)})
        return jsonify({"Members": [{"@odata.id": f"{P}/Outlets/{i}"} for i in outlets], "Members@odata.count": len(outlets)})

    @app.get(P + "/Outlets/<oid>")
    def outlet(oid):
        if oid in state.get("fail", set()):
            return jsonify(error={"code": "Base.1.21.GeneralError", "message": "busy"}), 503
        return jsonify(outlet_json(oid))

    @app.patch(P + "/Outlets/<oid>")
    def patch(oid):
        b = request.get_json(force=True)
        if "UserLabel" in b:  # echte FW 10.2.1: immer ErrorValidating
            info = [{"Message": "error during validation of given values", "MessageId": "Base.1.8.ErrorValidating",
                     "RelatedProperties": ["#/UserLabel"], "Resolution": "Please enter valid data"}]
            import json as _j
            return jsonify(error={"code": "Base.1.21.BadRequest", "message": _j.dumps(info)}), 400
        if "ElectricalConsumerNames" in b:
            outlets[oid]["consumers"] = b["ElectricalConsumerNames"]
        return jsonify(outlet_json(oid)), 200

    @app.post(P + "/Outlets/<oid>/Actions/Outlet.PowerControl")
    def power(oid):
        ps = request.get_json(force=True).get("PowerState")
        if ps not in ("On", "Off"):
            return jsonify(error={"code": "Base.1.21.ActionParameterValueNotInList", "message": f"The value {ps} is not in the list"}), 400
        state["actions"].append((oid, ps))
        outlets[oid]["on"] = ps == "On"
        return "", 204

    # ---- Rittal-Weboberfläche: /cgi-bin/json.cgi ----
    web = {"sessions": set(), "logins": 0, "sets": []}
    by_num = {o["num"]: oid for oid, o in outlets.items()}
    descs = [{"name": "Total.Frequency.Value"}] * 129 + [{"name": "Sockets.General.Power On Delay Multiplier"}]
    for n in range(1, n_outlets + 1):
        descs += [{"name": f"Sockets.Socket {n:02d}.General.DescName", "dataType": "string",
                   "constraints": {"maxLen": 20, "regExpr": "^([-_ a-zA-Z0-9]{0,20})$"}}] + \
                 [{"name": f"Sockets.Socket {n:02d}.General.{x}"} for x in ("Circuit", "Socket Type", "Relay", "Power On Delay", "Sequence Mode", "Grouping", "Status")] + [{}]

    @app.post("/cgi-bin/json.cgi")
    def jsoncgi():
        raw = request.get_data(as_text=True)
        cmd, _, payload = raw.partition("=")
        data = json.loads(payload or "{}")
        if cmd == "createSession":
            if data.get("username") != USER or data.get("password") != PASSWORD:
                return jsonify(result=2)
            t = secrets.token_hex(8)
            web["sessions"].add(t)
            web["logins"] += 1
            r = jsonify(result=0, user={"name": USER})
            r.set_cookie("sessionId", t, httponly=True)
            r.set_cookie("restSessionId", t)
            return r
        if request.cookies.get("sessionId") not in web["sessions"]:
            return jsonify(result=1)
        if cmd == "getDeviceInfos":
            return jsonify(result=0, deviceInfos=[{"deviceIndex": 1, "info": {"name": "PDU-Controller", "serialNumber": 42937128}},
                                                  {"deviceIndex": 2, "info": {"name": "PDU-SWI", "serialNumber": int(sid)}}])
        if cmd == "getDeviceDescriptions":
            return jsonify(result=0, deviceDescriptions=[{"deviceIndex": 1, "descriptions": [{"name": "x"}]},
                                                         {"deviceIndex": 2, "descriptions": descs}])
        if cmd == "setValues":
            for v in data.get("variables", []):
                d = descs[v["variableIndex"]] if v.get("deviceIndex") == 2 and v["variableIndex"] < len(descs) else {}
                import re as _re
                m = _re.match(r"Sockets\.Socket (\d+)\.General\.DescName", d.get("name", ""))
                if not m or not _re.fullmatch(d["constraints"]["regExpr"], str(v.get("value"))):
                    return jsonify(result=3)
                outlets[by_num[int(m.group(1))]]["label"] = v["value"]
                web["sets"].append((v["variableIndex"], v["value"]))
            return jsonify(result=0)
        return jsonify(result=0)

    app.web = web

    sensors = {f"Energy_L{k}": ("EnergykWh", "kWh", en[k], f"Line{k}ToNeutral") for k in (1, 2, 3)}
    sensors["Energy_AC1_Total"] = ("EnergykWh", "kWh", 10194415.0, "Total")
    sensors["Frequency_AC1"] = ("Frequency", "Hz", 50.0, "Total")

    @app.get(C + "/Sensors")
    def csensors():
        ids = list(sensors) + [f"Current_L{k}" for k in (1, 2, 3)] + [f"Current_L{o['phase']}_{o['num']}" for o in outlets.values()]
        return jsonify({"Members": [{"@odata.id": f"{C}/Sensors/{i}"} for i in ids]})

    @app.get(C + "/Sensors/<i>")
    def csensor(i):
        if i not in sensors:
            return jsonify({"Id": i, "Reading": 0.0})
        rt, u, v, ctx = sensors[i]
        return jsonify({"Id": i, "Name": i, "ReadingType": rt, "ReadingUnits": u, "Reading": v, "ElectricalContext": ctx})

    app.state = state
    app.outlets = outlets
    return app


class Running:
    def __init__(self, app, port=0):
        self.srv = make_server("127.0.0.1", port, app, threaded=True)
        self.port = self.srv.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self.app = app
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9101
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    random.seed(port)
    mode = sys.argv[3] if len(sys.argv) > 3 else ""
    app = make_rittal(n, sid=str(43090000 + port % 100), delay=float(sys.argv[4]) if len(sys.argv) > 4 else 0.0) if mode == "rittal" else make_pdu(n, expand=mode == "expand")
    app.run(port=port, threaded=True)
