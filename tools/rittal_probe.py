#!/usr/bin/env python3
"""Liest die interne JSON-Schnittstelle der Rittal-Weboberfläche (/cgi-bin/json.cgi) aus.

Zweck: herausfinden, wie die App Socket-Namen setzen kann (Redfish speichert sie bei FW 10.2.1 nicht).
Nur lesend – es wird nichts geändert. Nur Python-Standardbibliothek.

    python3 tools/rittal_probe.py 10.1.0.21 admin
      → fragt nach dem Passwort, legt ./rittal-probe/ an (ohne Passwort/Token)
"""
import getpass
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.request

host = sys.argv[1] if len(sys.argv) > 1 else input("PDU-Adresse: ")
user = sys.argv[2] if len(sys.argv) > 2 else input("Benutzer: ")
pw = getpass.getpass(f"Passwort für {user}@{host}: ")
url = f"https://{host}/cgi-bin/json.cgi"
out = "rittal-probe"
os.makedirs(out, exist_ok=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(jar))
extra_cookie = {}


def mask(text):
    text = text.replace(pw, "***") if pw else text
    return re.sub(r"eyJ[\w.\-]{20,}", "<token>", text)


def call(cmd, payload):
    body = f"{cmd}={json.dumps(payload, separators=(',', ':'))}".encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
        "Origin": f"https://{host}", "Referer": f"https://{host}/index.html", "Accept": "*/*"})
    if extra_cookie:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in extra_cookie.items()))
    with op.open(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
        return r.status, dict(r.headers), raw


print(f"→ createSession an {url}")
st, hdr, raw = call("createSession", {"username": user, "password": pw, "force": 0})
print("  Status", st, "| Set-Cookie-Namen:", [c.name for c in jar] or "keine")
print("  Antwort:", mask(raw)[:600])
open(f"{out}/1-createSession.txt", "w").write(f"Status {st}\nCookies: {[c.name for c in jar]}\nHeader: {mask(json.dumps(hdr))}\n\n{mask(raw)}")
if not list(jar):  # Token steht evtl. nur in der Antwort
    m = re.search(r"eyJ[\w.\-]{20,}", raw)
    if m:
        extra_cookie = {"sessionId": m.group(0), "restSessionId": m.group(0)}
        print("  Token aus der Antwort übernommen (als Cookie sessionId)")

for n, (cmd, payload) in enumerate([("getDeviceDescriptions", {"devices": []}), ("getDeviceInfos", {"devices": []}),
                                    ("getProcessData", {"lastTimestamp": 0}), ("getFeatures", {})], start=2):
    try:
        st, _, raw = call(cmd, payload)
    except Exception as e:  # noqa: BLE001
        print(f"→ {cmd}: Fehler {e}")
        continue
    fn = f"{out}/{n}-{cmd}.json"
    open(fn, "w").write(mask(raw))
    print(f"→ {cmd}: Status {st}, {len(raw)} Zeichen → {fn}")
    for m in list(re.finditer(r"Socket 0?2\b", raw))[:3]:  # Kontext rund um Socket 02 zeigen
        print("    …", mask(raw[max(0, m.start() - 200):m.end() + 200]).replace("\n", " "), "…")

print(f"\nFertig. Bitte den Ordner {out}/ als ZIP hochladen:  zip -r rittal-probe.zip {out}")
