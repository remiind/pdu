"""Interne JSON-Schnittstelle der Rittal-Weboberfläche (/cgi-bin/json.cgi) – nur zum Umbenennen von Sockets.

Hintergrund: Rittal FW 10.2.1 lehnt das Schreiben von `UserLabel` per Redfish ab. Die Weboberfläche setzt den
Namen über `setValues` auf die Variable `Sockets.Socket NN.General.DescName` (= Redfish-UserLabel).
Format: POST, `Content-Type: application/x-www-form-urlencoded`, Rumpf `befehl={json}` (JSON unkodiert, wie der Browser).
Anmeldung: `createSession={"username","password","force":0}` → Cookie `sessionId` (Auto-Logout nach 1800 s).
Variablen-Index = Position in `getDeviceDescriptions[...].descriptions` des Geräts (leere Einträge zählen mit).
"""
import json
import re
import threading
import time

import requests

TIMEOUT = (4, 15)
DEFAULT_RE = r"^([-_ a-zA-Z0-9]{0,20})$"
SESSION_MAX_AGE = 1500  # Sekunden; die PDU meldet nach 1800 s ab


class WebError(Exception):
    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.status = status


class RittalWeb:
    def __init__(self, base, user, password, verify_tls=False):
        self.url = base.rstrip("/") + "/cgi-bin/json.cgi"
        self.origin = base.rstrip("/")
        self.user, self.password = user, password
        self.http = requests.Session()
        self.http.verify = verify_tls
        self.lock = threading.Lock()
        self.logged_in = 0.0
        self._names = None  # {(serial, num): (deviceIndex, variableIndex, constraints)}
        self._names_t = 0.0

    def _post(self, cmd, payload):
        body = f"{cmd}={json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}".encode("utf-8")
        try:
            r = self.http.post(self.url, data=body, timeout=TIMEOUT, headers={
                "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
                "Origin": self.origin, "Referer": self.origin + "/index.html", "Accept": "*/*"})
        except requests.RequestException as e:
            raise WebError(f"Weboberfläche der PDU nicht erreichbar: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise WebError(f"Weboberfläche der PDU meldet HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise WebError("Weboberfläche der PDU liefert keine JSON-Antwort") from e

    def login(self):
        self.http.cookies.clear()
        j = self._post("createSession", {"username": self.user, "password": self.password, "force": 0})
        if j.get("result") != 0 or "sessionId" not in self.http.cookies:
            raise WebError(f"Anmeldung an der Rittal-Weboberfläche abgelehnt (result {j.get('result')})")
        self.logged_in = time.time()

    def call(self, cmd, payload):
        """Befehl mit Anmeldung; bei abgelaufener Sitzung (result ≠ 0) einmal neu anmelden."""
        with self.lock:
            if not self.logged_in or time.time() - self.logged_in > SESSION_MAX_AGE:
                self.login()
            j = self._post(cmd, payload)
            if j.get("result") != 0:
                self.login()
                j = self._post(cmd, payload)
            return j

    def _load_names(self):
        if self._names is not None and time.time() - self._names_t < 3600:
            return self._names
        infos = self.call("getDeviceInfos", {"devices": []}).get("deviceInfos") or []
        serial = {d.get("deviceIndex"): str((d.get("info") or {}).get("serialNumber")) for d in infos}
        descs = self.call("getDeviceDescriptions", {"devices": []}).get("deviceDescriptions") or []
        names = {}
        for dev in descs:
            di = dev.get("deviceIndex")
            for vi, v in enumerate(dev.get("descriptions") or []):
                m = re.match(r"^Sockets\.Socket (\d+)\.General\.DescName$", str(v.get("name", "")))
                if m:
                    names[(serial.get(di), int(m.group(1)))] = (di, vi, v.get("constraints") or {})
                    names.setdefault((None, int(m.group(1))), (di, vi, v.get("constraints") or {}))
        if not names:
            raise WebError("Die Weboberfläche der PDU kennt keine Socket-Namen (DescName)")
        self._names, self._names_t = names, time.time()
        return names

    def name_var(self, serial, num):
        names = self._load_names()
        hit = names.get((str(serial), int(num))) or names.get((None, int(num)))
        if not hit:
            raise WebError(f"Socket {num} ist in der Weboberfläche der PDU nicht zu finden", 404)
        return hit

    @staticmethod
    def check(name, constraints=None):
        c = constraints or {}
        rx = c.get("regExpr") or DEFAULT_RE
        maxlen = c.get("maxLen") or 20
        if len(name) > maxlen or not re.fullmatch(rx, name):
            raise WebError(f"Name nicht erlaubt: höchstens {maxlen} Zeichen, nur Buchstaben A–Z, Ziffern, Leerzeichen, - und _", 400)

    def set_name(self, serial, num, name):
        di, vi, cons = self.name_var(serial, num)
        self.check(name, cons)
        j = self.call("setValues", {"variables": [{"deviceIndex": di, "variableIndex": vi, "value": name}]})
        if j.get("result") != 0:
            raise WebError(f"Die PDU hat den Namen nicht übernommen (result {j.get('result')})", 409)
