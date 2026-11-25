"""Redfish-Client für Rittal-PDUs (Firmware 10.x, DMTF-Schemas PowerDistribution/Outlet/Circuit).

Ein `PduClient` je konfigurierter PDU. Anmeldung über eine Redfish-Session (X-Auth-Token),
die wiederverwendet und bei 401 einmal erneuert wird; klappt das Anlegen einer Session nicht,
wird HTTP-Basic verwendet. Messwerte werden kurz zwischengespeichert, damit mehrere offene
Browser die PDU nicht mehrfach abfragen.
"""
import concurrent.futures as cf
import json
import re
import threading
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT = (4, 12)  # Verbindungsaufbau, Antwort
ACTIONS = {"On", "Off", "PowerCycle"}
AUTH_BACKOFF = 300  # Sekunden Pause nach fehlgeschlagener Anmeldung
CYCLE_PAUSE = 5  # Sekunden aus beim nachgebildeten Neustart (PDU kennt kein PowerCycle)
# Chassis-Sensoren, die zusätzlich gelesen werden (nicht die je Socket)
SENSOR_RE = re.compile(r"^(Energy_L[123]|Energy_(AC\d+_)?Total|Energy_AC\d+|Frequency.*|Current_N.*|Current_Neutral.*|Temperature.*|Humidity.*)$", re.I)
PHASE_RE = re.compile(r"(?:\bLine|\bL|\bPhase ?)([123])(?!\d)")


class RedfishError(Exception):
    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.status = status


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return _num(v.get("Reading"))
    return None


def _odata(x):
    return x.get("@odata.id") if isinstance(x, dict) else None


def _health(st):
    st = st if isinstance(st, dict) else {}
    return {"health": st.get("Health") or st.get("HealthRollup"), "status": st.get("State")}


def _phase(*vals):
    """'Line1', 'Line2ToNeutral', 'L3' … → 'L1'…'L3' (sonst None)."""
    for v in vals:
        if isinstance(v, str):
            m = PHASE_RE.search(v)
            if m:
                return "L" + m.group(1)
    return None


def _poly(obj, key):
    """PolyPhase…-Feld → {'L1': x, 'L2': y, 'L3': z, 'N': n} (nur vorhandene Werte)."""
    raw = obj.get(key)
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        val = _num(v)
        if val is None:
            continue
        if k == "Neutral":
            out["N"] = val
            continue
        ph = _phase(k)
        # Line1ToNeutral bevorzugen, Line1ToLine2 (Außenleiterspannung) nur, wenn sonst nichts da ist
        if ph and (ph not in out or "Neutral" in k):
            out[ph] = val
    return out


def _outlet_type(v):
    if not isinstance(v, str) or not v:
        return None
    m = re.search(r"C\s?(13|14|15|19|20|21)", v)
    if m:
        return "C" + m.group(1)
    if "CEE_7" in v or "Schuko" in v:
        return "Schuko"
    return v.replace("_", " ")


class PduClient:
    def __init__(self, key, name, host, user, password, verify_tls=False, cache_s=2.0):
        self.key, self.name, self.host = key, name, host
        self.base = host if re.match(r"^https?://", host) else f"https://{host}"
        self.base = self.base.rstrip("/")
        self.user, self.password = user, password
        self.http = requests.Session()
        self.http.verify = verify_tls
        self.http.headers.update({"Accept": "application/json", "OData-Version": "4.0"})
        self.token = None
        self.session_uri = None
        self.basic = False
        self.lock = threading.Lock()
        self.cache_s = cache_s
        self._snap = None
        self._snap_t = 0.0
        self._snap_lock = threading.Lock()
        self._pdu_path = None
        self._sens_ids = None  # Chassis-Sensoren (Energie je Phase, Frequenz …)
        self._sens_t = 0.0
        self.last_error = None
        self.last_ok = 0.0
        self.auth_mode = None
        self.label_field = None  # UserLabel oder ElectricalConsumerNames (wird beim ersten Umbenennen erkannt)
        self.auth_block = 0.0  # nach fehlgeschlagener Anmeldung bis zu diesem Zeitpunkt keine neuen Versuche
        self.auth_error = None

    # ---------- HTTP ----------
    def _login(self):
        """Session anlegen; lehnt die PDU das ab, wird HTTP-Basic probiert. Fehler nennen Code + Text der PDU.

        Schlägt beides fehl, wird AUTH_BACKOFF Sekunden lang nicht neu angemeldet (die PDU könnte den
        Account sonst nach zu vielen Fehlversuchen sperren).
        """
        if time.time() < self.auth_block:
            raise RedfishError(f"{self.auth_error} – nächster Versuch in {int(self.auth_block - time.time())} s", 502)
        try:
            r = self.http.post(self.base + "/redfish/v1/SessionService/Sessions",
                               json={"UserName": self.user, "Password": self.password}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise RedfishError(f"PDU nicht erreichbar ({self.host}): {e.__class__.__name__}") from e
        token = r.headers.get("X-Auth-Token")
        if r.status_code in (200, 201, 204) and token:
            self.token, self.basic = token, False
            self.session_uri = r.headers.get("Location")
            self.auth_mode = "Session"
            return
        sess_err = self._err_text(r)
        # Rückfall: Basic-Auth an einer geschützten Ressource prüfen
        try:
            b = self.http.get(self.base + "/redfish/v1/PowerEquipment/RackPDUs", auth=(self.user, self.password), timeout=TIMEOUT)
        except requests.RequestException as e:
            raise RedfishError(f"PDU nicht erreichbar ({self.host}): {e.__class__.__name__}") from e
        if b.status_code < 400:
            self.token, self.basic = None, True
            self.auth_mode = "Basic"
            return
        if not self.user or not self.password:
            raise RedfishError("PDU_USER/PDU_PASSWORD sind im Container nicht gesetzt (.env prüfen)", 502)
        self.auth_error = f"Anmeldung an der PDU fehlgeschlagen – Session: {sess_err}; Basic: {self._err_text(b)}"
        self.auth_block = time.time() + AUTH_BACKOFF
        raise RedfishError(self.auth_error, 502)

    def _auth_headers(self):
        if self.token:
            return {"X-Auth-Token": self.token}
        return {}

    def request(self, method, path, body=None, retry=True):
        if not path.startswith("/"):
            path = "/" + path
        with self.lock:
            if not self.token and not self.basic:
                self._login()
        try:
            r = self.http.request(method, self.base + path, json=body, headers=self._auth_headers(),
                                  auth=(self.user, self.password) if self.basic else None, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise RedfishError(f"PDU nicht erreichbar ({self.host}): {e.__class__.__name__}") from e
        if r.status_code == 401 and retry:
            with self.lock:
                self.token, self.basic = None, False
                self._login()
            return self.request(method, path, body, retry=False)
        if r.status_code >= 400:
            raise RedfishError(self._err_text(r), 502 if r.status_code >= 500 else r.status_code if r.status_code in (400, 403, 404, 405, 409) else 502)
        if r.status_code == 204 or not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {"_text": r.text[:2000]}

    @staticmethod
    def _err_text(r):
        """Fehlertext der PDU lesbar machen (Rittal packt ExtendedInfo teils als Liste in „message“)."""
        try:
            j = r.json().get("error", {})
        except (ValueError, AttributeError):
            return f"PDU meldet HTTP {r.status_code}"
        infos = j.get("@Message.ExtendedInfo") or []
        msg = j.get("message")
        if isinstance(msg, list):
            infos = infos or msg
            msg = None
        if isinstance(msg, str) and msg.lstrip().startswith("["):
            try:
                infos = infos or json.loads(msg)
                msg = None
            except ValueError:
                pass
        parts = []
        for i in infos if isinstance(infos, list) else []:
            if isinstance(i, dict) and i.get("Message"):
                t = i["Message"]
                props = [str(x).lstrip("#/") for x in i.get("RelatedProperties") or []]
                if props:
                    t += f" ({', '.join(props)})"
                parts.append(t)
        text = "; ".join(parts) or (msg if isinstance(msg, str) else None)
        return f"PDU meldet {r.status_code}: {text}" if text else f"PDU meldet HTTP {r.status_code}"

    def get(self, path):
        return self.request("GET", path)

    def logout(self):
        if self.token and self.session_uri:
            try:
                self.http.delete(self.base + self.session_uri if self.session_uri.startswith("/") else self.session_uri,
                                 headers=self._auth_headers(), timeout=3)
            except requests.RequestException:
                pass
        self.token = None

    # ---------- Ressourcen ----------
    def pdu_path(self):
        if not self._pdu_path:
            col = self.get("/redfish/v1/PowerEquipment/RackPDUs")
            members = col.get("Members") or []
            if not members:
                raise RedfishError("Die PDU meldet keine RackPDUs", 502)
            self._pdu_path = _odata(members[0])
        return self._pdu_path

    def collection(self, path):
        """Alle Mitglieder einer Collection, parallel einzeln geladen.

        Kein $expand: Rittal (FW 10.2.1) meldet ExpandQuery, liefert dabei aber gekürzte Mitglieder
        (ohne PowerState, OutletType, UserLabel, Actions).
        """
        col = self.get(path)
        links = [_odata(m) for m in col.get("Members") or [] if _odata(m)]
        if not links:
            return []
        with cf.ThreadPoolExecutor(max_workers=min(12, len(links))) as ex:
            return list(ex.map(self.get, links))

    def _opt(self, fn, *a):
        try:
            return fn(*a)
        except RedfishError:
            return None

    # ---------- Momentaufnahme ----------
    def snapshot(self, force=False):
        with self._snap_lock:
            if not force and self._snap and time.time() - self._snap_t < self.cache_s:
                return self._snap
            try:
                snap = self._build()
            except RedfishError as e:
                self.last_error = str(e)
                raise
            self.last_error = None
            self.last_ok = time.time()
            self._snap, self._snap_t = snap, time.time()
            return snap

    def invalidate(self):
        self._snap_t = 0.0

    def summary(self, max_age=15.0):
        """Kurzstatus für die PDU-Auswahl: nutzt eine frische Momentaufnahme, sonst nur PDU + Metrics."""
        snap = self._snap
        if snap and time.time() - self._snap_t < max_age:
            return {"power": snap["totals"]["power"], "on": snap["counts"]["on"], "outlets": snap["counts"]["outlets"],
                    "model": snap["info"]["model"], "health": snap["info"]["health"]}
        pp = self.pdu_path()
        pdu = self.get(pp)
        met = self._opt(self.get, _odata(pdu.get("Metrics")) or pp + "/Metrics") or {}
        self.last_ok, self.last_error = time.time(), None
        return {"power": _num(met.get("PowerWatts")), "on": None, "outlets": None, "model": pdu.get("Model"),
                "health": _health(pdu.get("Status"))["health"]}

    def chassis_sensor_ids(self, pdu):
        """IDs interessanter Sensoren im Chassis der PDU (Liste 10 min zwischengespeichert)."""
        if self._sens_ids is not None and time.time() - self._sens_t < 600:
            return self._sens_ids
        ch = [_odata(c) for c in ((pdu.get("Links") or {}).get("Chassis") or []) if _odata(c)]
        paths = [_odata(pdu.get("Sensors"))] if pdu.get("Sensors") else []
        paths += [c + "/Sensors" for c in ch]
        ids = []
        for sp in paths:
            try:
                col = self.get(sp)
            except RedfishError:
                continue
            for m in col.get("Members") or []:
                u = _odata(m)
                if u and SENSOR_RE.search(u.rsplit("/", 1)[-1]):
                    ids.append(u)
            if ids:
                break
        self._sens_ids, self._sens_t = ids[:40], time.time()
        return self._sens_ids

    def _build(self):
        pp = self.pdu_path()
        pdu = self.get(pp)
        link = lambda k, d: _odata(pdu.get(k)) or f"{pp}/{d}"  # noqa: E731
        jobs = {
            "mains": (self.collection, link("Mains", "Mains")),
            "branches": (self.collection, link("Branches", "Branches")),
            "outlets": (self.collection, link("Outlets", "Outlets")),
            "metrics": (self.get, link("Metrics", "Metrics")),
            "sensors": (self._chassis_sensors, pdu),
        }
        res = {}
        with cf.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futs = {k: ex.submit(self._opt, fn, arg) for k, (fn, arg) in jobs.items()}
            for k, f in futs.items():
                res[k] = f.result()
        return normalize(self, pdu, res)

    def diagnose(self):
        """Rohdaten für die Fehlersuche: alle relevanten Ressourcen einer PDU (ohne Zugangsdaten)."""
        out, t0 = {"host": self.host, "auth_mode": None, "expand": None, "errors": {}, "timing": {}, "data": {}}, time.time()

        def grab(path, label=None):
            t = time.time()
            try:
                out["data"][label or path] = self.get(path)
            except RedfishError as e:
                out["errors"][label or path] = str(e)
            out["timing"][label or path] = round(time.time() - t, 2)
            return out["data"].get(label or path) or {}

        def members(col):
            return [_odata(m) for m in (col.get("Members") or []) if _odata(m)]

        grab("/redfish/v1")
        grab("/redfish/v1/PowerEquipment")
        col = grab("/redfish/v1/PowerEquipment/RackPDUs")
        pp = (members(col) or [None])[0]
        if not pp:
            return out
        pdu = grab(pp)
        for name, n in (("Mains", 9), ("Branches", 9), ("Outlets", 3), ("Sensors", 12)):
            c = grab(_odata(pdu.get(name)) or f"{pp}/{name}")
            for m in members(c)[:n]:
                grab(m)
        grab(_odata(pdu.get("Metrics")) or f"{pp}/Metrics")
        # Sensor-Verweise (DataSourceUri) aus Outlet/Mains/Branch nachladen
        refs = set()

        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k == "DataSourceUri" and isinstance(x, str):
                        refs.add(x)
                    else:
                        walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
        walk({k: v for k, v in out["data"].items() if "/Outlets/" in k or "/Mains/" in k or "/Branches/" in k})
        for r in sorted(refs)[:40]:
            if r not in out["data"]:
                grab(r)
        # $expand testen
        t = time.time()
        try:
            e = self.get(f"{pp}/Outlets?$expand=.($levels=1)")
            mem = e.get("Members") or []
            out["expand"] = {"ok": bool(mem) and all("Id" in m for m in mem if isinstance(m, dict)), "members": len(mem), "sample": mem[:1]}
        except RedfishError as ex:
            out["expand"] = {"ok": False, "error": str(ex)}
        out["timing"]["$expand Outlets"] = round(time.time() - t, 2)
        out["auth_mode"] = self.auth_mode
        out["timing"]["gesamt"] = round(time.time() - t0, 2)
        return out

    def _chassis_sensors(self, pdu):
        ids = self.chassis_sensor_ids(pdu)
        if not ids:
            return []
        with cf.ThreadPoolExecutor(max_workers=min(8, len(ids))) as ex:
            return [x for x in ex.map(lambda u: self._opt(self.get, u), ids) if x]

    # ---------- Schalten / Beschriften ----------
    def _outlet(self, oid):
        if not re.match(r"^[\w.-]{1,40}$", str(oid)):
            raise RedfishError("Ungültige Socket-ID", 400)
        return f"{self.pdu_path()}/Outlets/{oid}"

    def power(self, oid, action):
        if action not in ACTIONS:
            raise RedfishError("Unbekannte Aktion", 400)
        path = self._outlet(oid)
        target = path + "/Actions/Outlet.PowerControl"
        snap = self._snap
        o = None
        if snap:
            o = next((x for x in snap["outlets"] if x["id"] == str(oid)), None)
            if o and o.get("target"):
                target = o["target"]
            if o and o.get("locked"):
                raise RedfishError("Socket ist gegen Schalten gesperrt", 409)
        allowed = (o or {}).get("allowed") if snap else None
        if action == "PowerCycle" and allowed and "PowerCycle" not in allowed:
            self.request("POST", target, {"PowerState": "Off"})
            time.sleep(CYCLE_PAUSE)
            self.request("POST", target, {"PowerState": "On"})
        else:
            self.request("POST", target, {"PowerState": action})
        self.invalidate()

    def can_cycle(self, oid):
        o = next((x for x in (self._snap or {}).get("outlets", []) if x["id"] == str(oid)), None)
        return not o or not o.get("allowed") or "PowerCycle" in o["allowed"]

    def post_state(self, oid, state):
        """Einzelner Schaltbefehl ohne Neustart-Logik (für nachgebildeten Sammel-Neustart)."""
        o = next((x for x in (self._snap or {}).get("outlets", []) if x["id"] == str(oid)), None)
        if o and o.get("locked"):
            raise RedfishError("Socket ist gegen Schalten gesperrt", 409)
        self.request("POST", (o or {}).get("target") or self._outlet(oid) + "/Actions/Outlet.PowerControl", {"PowerState": state})

    def set_label(self, oid, label):
        """Beschreibung setzen. Rittal (FW 10.2.1) lehnt jedes PATCH auf UserLabel mit ErrorValidating ab,
        nimmt aber ElectricalConsumerNames an → dorthin schreiben (leer = zurück auf UserLabel der PDU)."""
        if self.label_field != "ElectricalConsumerNames":
            try:
                self.request("PATCH", self._outlet(oid), {"UserLabel": label})
                self.label_field = "UserLabel"
                self.invalidate()
                return
            except RedfishError as e:
                if e.status != 400:
                    raise
            self.label_field = "ElectricalConsumerNames"
        self.request("PATCH", self._outlet(oid), {"ElectricalConsumerNames": [label] if label else []})
        self.invalidate()
        # Rittal antwortet 200, speichert aber evtl. nicht → nachlesen
        o = self.get(self._outlet(oid))
        names = [n for n in (o.get("ElectricalConsumerNames") or []) if isinstance(n, str) and n.strip()]
        if label and label not in names:
            raise RedfishError("Die PDU hat die Änderung mit „OK“ beantwortet, aber nicht gespeichert "
                               "(ElectricalConsumerNames bleibt leer) – Umbenennen per Redfish geht mit dieser Firmware nicht", 409)


def normalize(client, pdu, res):
    """Redfish-Rohdaten → kompaktes JSON für die Oberfläche."""
    loc = pdu.get("Location") or {}
    place = loc.get("Placement") or {}
    addr = loc.get("PostalAddress") or {}
    # Platzhalter der Rittal-Werkseinstellung („Rack“, „Location“) nicht anzeigen
    cand = {"Row": place.get("Row"), "Rack": place.get("Rack"), "RackOffset": place.get("RackOffset") and str(place.get("RackOffset")),
            "Location": place.get("AdditionalInfo"), "Room": addr.get("Room"), "Building": addr.get("Building")}
    location = " · ".join(v for k, v in cand.items() if v and str(v).strip().lower() != k.lower())
    info = {
        "rfName": pdu.get("Name"), "model": " ".join(x for x in [pdu.get("PartNumber"), pdu.get("Description") or pdu.get("Model")] if x) or pdu.get("Model"),
        "manufacturer": pdu.get("Manufacturer"),
        "serial": pdu.get("SerialNumber"), "part": pdu.get("PartNumber"), "firmware": pdu.get("FirmwareVersion"),
        "asset": pdu.get("AssetTag"), "userLabel": pdu.get("UserLabel"), "location": location or None,
        "equipmentType": pdu.get("EquipmentType"), **_health(pdu.get("Status")),
    }

    # --- Einspeisung (Mains): entweder ein Mehrphasen-Kreis oder je Phase ein Kreis ---
    phases, totals = {}, {"power": None, "energy": None, "frequency": None, "neutral": None, "current": None}
    mains = res.get("mains") or []
    for c in mains:
        pv, pc, pp, pe = (_poly(c, k) for k in ("PolyPhaseVoltage", "PolyPhaseCurrentAmps", "PolyPhasePowerWatts", "PolyPhaseEnergykWh"))
        rated = _num(c.get("RatedCurrentAmps")) or None
        if pv or pc or pp or pe:
            for ph in ("L1", "L2", "L3"):
                if any(ph in d for d in (pv, pc, pp, pe)):
                    phases[ph] = {"name": ph, "voltage": pv.get(ph), "current": pc.get(ph), "power": pp.get(ph), "energy": pe.get(ph), "rated": rated}
            if "N" in pc:
                totals["neutral"] = pc["N"]
        else:
            ph = _phase(c.get("ElectricalContext"), c.get("Name"), c.get("Id")) or str(c.get("Name") or c.get("Id"))
            phases[ph] = {"name": ph, "voltage": _num(c.get("Voltage")), "current": _num(c.get("CurrentAmps")), "power": _num(c.get("PowerWatts")),
                          "energy": _num(c.get("EnergykWh")), "rated": rated}
        if totals["frequency"] is None:
            totals["frequency"] = _num(c.get("FrequencyHz"))
        if len(mains) == 1 and (pv or pc):
            totals["power"] = _num(c.get("PowerWatts"))
            totals["energy"] = _num(c.get("EnergykWh"))
            totals["current"] = _num(c.get("CurrentAmps"))
    phase_list = [phases[k] for k in sorted(phases, key=lambda s: (not s.startswith("L"), s))]

    # Chassis-Sensoren: Energie je Phase, Frequenz, Neutralleiter, Umwelt
    sens = [x for x in (res.get("sensors") or []) if isinstance(x, dict)]
    sens_energy = {}
    for x in sens:
        sid, rt, val = str(x.get("Id") or ""), x.get("ReadingType"), _num(x.get("Reading"))
        if val is None:
            continue
        ph = _phase(x.get("ElectricalContext"), sid)
        if rt in ("EnergykWh", "EnergyJoules", "EnergyWh") or sid.lower().startswith("energy"):
            kwh = val / 3.6e6 if rt == "EnergyJoules" else val / 1000 if (rt == "EnergyWh" or x.get("ReadingUnits") == "Wh") else val
            if ph and "Total" not in str(x.get("ElectricalContext")):
                sens_energy[ph] = kwh
            else:
                sens_energy["total"] = kwh
        elif rt == "Frequency" or sid.lower().startswith("frequency"):
            totals["frequency"] = totals["frequency"] or val
        elif sid.lower().startswith("current_n"):
            totals["neutral"] = val
    for p in phase_list:
        if p["name"] in sens_energy:
            p["energy"] = sens_energy[p["name"]]
    if len(mains) == 1 and any(p["energy"] is not None for p in phase_list):
        totals["energy"] = None  # Mains.EnergykWh zeigt bei Rittal auf einen einzelnen Phasenzähler

    met = res.get("metrics") or {}
    if _num(met.get("PowerWatts")) is not None:
        totals["power"] = _num(met.get("PowerWatts"))
    met_e = _num(met.get("EnergykWh"))
    if totals["power"] is None and phase_list and any(p["power"] is not None for p in phase_list):
        totals["power"] = sum(p["power"] or 0 for p in phase_list)
    if totals["energy"] is None and phase_list and all(p["energy"] is not None for p in phase_list):
        totals["energy"] = sum(p["energy"] for p in phase_list)
    if totals["energy"] is None and "total" in sens_energy and sens_energy["total"] < 1e6:
        totals["energy"] = sens_energy["total"]
    if totals["energy"] is None and met_e is not None and met_e < 1e6:
        totals["energy"] = met_e  # Rittal liefert hier teils einen Zähler in falscher Einheit (z. B. 10.194.415)
    env = {"temperature": _num(met.get("TemperatureCelsius")), "humidity": _num(met.get("HumidityPercent"))}

    # --- Abgänge / Sicherungen ---
    branches = []
    for c in res.get("branches") or []:
        branches.append({
            "id": str(c.get("Id")), "name": c.get("UserLabel") or c.get("Name") or c.get("Id"),
            "phase": _phase(c.get("ElectricalContext"), c.get("Name"), c.get("Id")),
            "breaker": c.get("BreakerState"), "powerState": c.get("PowerState"), "rated": _num(c.get("RatedCurrentAmps")) or None,
            "current": _num(c.get("CurrentAmps")), "power": _num(c.get("PowerWatts")), "energy": _num(c.get("EnergykWh")),
            "voltage": _num(c.get("Voltage")), **_health(c.get("Status")),
        })

    # --- Sockets ---
    branch_phase = {}
    for b in res.get("branches") or []:
        if _odata(b) and _phase(b.get("ElectricalContext"), b.get("Name"), b.get("Id")):
            branch_phase[_odata(b)] = _phase(b.get("ElectricalContext"), b.get("Name"), b.get("Id"))
    outlets = []
    for o in res.get("outlets") or []:
        oid = str(o.get("Id"))
        links = o.get("Links") or {}
        branch = _odata(links.get("BranchCircuit"))
        act = (o.get("Actions") or {}).get("#Outlet.PowerControl") or {}
        allowed = act.get("PowerState@Redfish.AllowableValues")
        m = re.search(r"(\d+)\s*$", oid) or re.search(r"(\d+)\s*$", str(o.get("Name") or ""))
        outlets.append({
            "id": oid, "num": int(m.group(1)) if m else None,
            "name": o.get("Name"),
            # Anzeige: Gerätename (ElectricalConsumerNames, per API beschreibbar) vor der Rittal-Beschreibung (UserLabel)
            "label": next((n for n in (o.get("ElectricalConsumerNames") or []) if isinstance(n, str) and n.strip()), None) or o.get("UserLabel") or "",
            "pduLabel": o.get("UserLabel") or "",
            "type": _outlet_type(o.get("OutletType")), "state": o.get("PowerState"),
            "locked": bool(o.get("PowerControlLocked")),
            "phase": _phase(o.get("ElectricalContext")) or branch_phase.get(branch),
            "branch": branch.rstrip("/").rsplit("/", 1)[-1] if branch else None,
            "current": _num(o.get("CurrentAmps")), "power": _num(o.get("PowerWatts")), "energy": _num(o.get("EnergykWh")),
            "voltage": _num(o.get("Voltage")), "rated": _num(o.get("RatedCurrentAmps")) or None,
            "target": act.get("target"), "allowed": allowed if isinstance(allowed, list) else None, **_health(o.get("Status")),
        })
    outlets.sort(key=lambda x: (x["num"] is None, x["num"] or 0, x["id"]))
    # Keine Messung je Socket (Rittal PDU-SWI/MAN meldet überall 0,0 A, aber Phasen tragen Last) → als „nicht gemessen“ behandeln
    metering = any((o["current"] or 0) > 0 or (o["power"] or 0) > 0 for o in outlets)
    phase_load = any((p["current"] or 0) > 0.05 for p in phase_list)
    if not metering and phase_load:
        for o in outlets:
            o["current"] = o["power"] = o["energy"] = None
    if totals["power"] is None and outlets and any(o["power"] is not None for o in outlets):
        totals["power"] = sum(o["power"] or 0 for o in outlets)

    sensors = []
    for s in sens:
        rt = s.get("ReadingType")
        if rt in ("Temperature", "Humidity", "AirFlow", "Pressure", "Altitude", "AbsoluteHumidity", "LeakDetection", "Contact"):
            if _num(s.get("Reading")) is None:
                continue
            sensors.append({"id": str(s.get("Id")), "name": s.get("Name") or s.get("Id"), "type": rt, "reading": _num(s.get("Reading")),
                            "units": s.get("ReadingUnits"), **_health(s.get("Status"))})

    return {
        "key": client.key, "name": client.name, "host": client.host, "ts": time.time(),
        "info": info, "phases": phase_list, "totals": totals, "env": env,
        "branches": branches, "outlets": outlets, "sensors": sensors,
        "metering": metering,
        "counts": {"outlets": len(outlets), "on": sum(1 for o in outlets if o["state"] == "On"), "off": sum(1 for o in outlets if o["state"] == "Off")},
    }
