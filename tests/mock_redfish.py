"""Simulierte Rittal-PDU (Redfish) für Tests und lokale Entwicklung.

    python tests/mock_redfish.py 9101 42     # PDU mit 42 Sockets auf http://127.0.0.1:9101
"""
import random
import secrets
import sys
import threading

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

USER, PASSWORD = "apiuser", "mock-secret-123"


def make_pdu(n_outlets=42, expand=False, model="PDU-SWI 7979340"):
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
    make_pdu(n, expand=len(sys.argv) > 3).run(port=port, threaded=True)
