"""PDU – Monitoring und Socket-Steuerung für Rittal-PDUs über Redfish. Flask-App (SPA + JSON-API)."""
import concurrent.futures as cf
import fcntl
import json
import os
import re
import time

from flask import Flask, abort, g, jsonify, redirect, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import can_control, init_auth
from . import redfish
from .redfish import ACTIONS, PduClient, RedfishError

VERSION = "0.2.0"
LABEL_MAX = 64

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "pdu"


def parse_list(raw):
    """PDU_LIST: Einträge „Gruppe/Name=Host“ getrennt durch ; , oder Zeilenumbruch (Gruppe optional)."""
    out, seen = [], set()
    for part in re.split(r"[;,\n]", raw or ""):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, host = (x.strip() for x in part.split("=", 1))
        group = ""
        if "/" in name:
            group, name = (x.strip() for x in name.split("/", 1))
        if not name or not host:
            continue
        key = slug(name)
        while key in seen:
            key += "-2"
        seen.add(key)
        out.append({"key": key, "name": name, "group": group, "host": host})
    return out


def create_app(data_dir=None, pdus=None, secure_cookies=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    data_dir = data_dir or os.environ.get("PDU_DATA", "/data")
    os.makedirs(data_dir, exist_ok=True)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    if os.environ.get("PDU_TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if secure_cookies is None:
        secure_cookies = os.environ.get("PDU_COOKIE_SECURE", "1") != "0"
    require_https = os.environ.get("PDU_REQUIRE_HTTPS") == "1"

    # ---------- PDUs aus der Umgebung ----------
    conf = pdus if pdus is not None else parse_list(os.environ.get("PDU_LIST", ""))
    user = os.environ.get("PDU_USER", "")
    password = os.environ.get("PDU_PASSWORD", "")
    verify = os.environ.get("PDU_VERIFY_TLS", "0") == "1"
    cache_s = float(os.environ.get("PDU_CACHE_SECONDS", "2"))
    clients = {}
    for c in conf:
        clients[c["key"]] = PduClient(c["key"], c["name"], c["host"], c.get("user") or user, c.get("password") or password, verify, cache_s)
    app.pdus = conf
    app.clients = clients
    log_path = os.path.join(data_dir, "actions.jsonl")

    def require_json():
        if not request.is_json:
            abort(415)
        return request.get_json(silent=True)

    init_auth(app, data_dir, require_json, secure_cookies)

    @app.before_request
    def https_only():
        if require_https and not request.is_secure and request.path != "/healthz":
            if request.method in ("GET", "HEAD"):
                return redirect(request.url.replace("http://", "https://", 1), 308)
            abort(400, "Nur über HTTPS")
        return None

    def client(key):
        c = clients.get(key)
        if not c:
            abort(404, "PDU nicht gefunden")
        return c

    def need_control():
        if not can_control(g.user):
            abort(403, "Dein Konto darf nur ansehen, nicht schalten")

    def audit(pdu, outlet, action, ok, err=None, label=None):
        entry = {"ts": int(time.time()), "user": g.user["name"], "pdu": pdu.key, "pduName": pdu.name,
                 "outlet": outlet, "label": label, "action": action, "ok": ok}
        if err:
            entry["error"] = err
        with open(log_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fcntl.flock(fh, fcntl.LOCK_UN)
        # auf ~5000 Zeilen begrenzen
        try:
            if os.path.getsize(log_path) > 1_500_000:
                with open(log_path, "r+", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    lines = fh.readlines()[-3000:]
                    fh.seek(0)
                    fh.truncate()
                    fh.writelines(lines)
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass

    def label_of(c, oid):
        snap = c._snap
        o = next((x for x in (snap or {}).get("outlets", []) if x["id"] == str(oid)), None)
        return (o.get("label") or o.get("name")) if o else None

    @app.errorhandler(RedfishError)
    def redfish_error(e):
        return jsonify(error=str(e)), e.status if e.status in (400, 403, 404, 405, 409) else 502

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(415)
    @app.errorhandler(429)
    def http_error(e):
        if request.path.startswith("/api/"):
            msg = e.description if isinstance(e.description, str) and not e.description.startswith(("The ", "Bad ", "Unsupported ")) else e.name
            return jsonify(error=msg), e.code
        return e

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.is_secure:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        resp.headers["Cache-Control"] = "no-cache" if request.path.startswith("/static/") else "no-store"
        return resp

    # ---------- Seiten ----------
    @app.get("/")
    def index():
        return send_from_directory(os.path.join(app.root_path, "templates"), "index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, version=VERSION)

    # ---------- API ----------
    @app.get("/api/pdus")
    def list_pdus():
        return jsonify(pdus=[{**p, "error": clients[p["key"]].last_error} for p in conf], control=can_control(g.user), version=VERSION)

    @app.get("/api/pdus/summary")
    def summary():
        def one(p):
            c = clients[p["key"]]
            try:
                return p["key"], {"ok": True, **c.summary()}
            except RedfishError as e:
                c.last_error = str(e)
                return p["key"], {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001 – eine PDU darf die Liste nicht kaputt machen
                app.logger.exception("summary %s", p["key"])
                return p["key"], {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
        if not conf:
            return jsonify({})
        with cf.ThreadPoolExecutor(max_workers=len(conf)) as ex:
            return jsonify(dict(ex.map(one, conf)))

    @app.get("/api/pdus/<key>")
    def pdu_data(key):
        c = client(key)
        return jsonify(c.snapshot(force=request.args.get("force") == "1"))

    @app.post("/api/pdus/<key>/outlets/<oid>/power")
    def outlet_power(key, oid):
        need_control()
        c = client(key)
        action = (require_json() or {}).get("action")
        if action not in ACTIONS:
            abort(400, "Aktion muss On, Off oder PowerCycle sein")
        lbl = label_of(c, oid)
        try:
            c.power(oid, action)
        except RedfishError as e:
            audit(c, oid, action, False, str(e), lbl)
            raise
        audit(c, oid, action, True, label=lbl)
        return jsonify(ok=True)

    @app.post("/api/pdus/<key>/power")
    def bulk_power(key):
        need_control()
        c = client(key)
        body = require_json() or {}
        action, ids = body.get("action"), body.get("ids")
        if action not in ACTIONS:
            abort(400, "Aktion muss On, Off oder PowerCycle sein")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 64 or not all(isinstance(i, str) for i in ids):
            abort(400, "Keine Sockets gewählt")
        results = {}
        if action == "PowerCycle" and not all(c.can_cycle(i) for i in ids):
            # PDU kennt kein PowerCycle → alle aus, kurz warten, alle wieder an (nicht jeden Socket einzeln 5 s)
            for oid in ids:
                try:
                    c.post_state(oid, "Off")
                    results[oid] = {"ok": True}
                except RedfishError as e:
                    results[oid] = {"ok": False, "error": str(e)}
            time.sleep(redfish.CYCLE_PAUSE)
            for oid in ids:
                if results[oid]["ok"]:
                    try:
                        c.post_state(oid, "On")
                    except RedfishError as e:
                        results[oid] = {"ok": False, "error": f"wieder einschalten: {e}"}
            c.invalidate()
            for oid in ids:
                audit(c, oid, action, results[oid]["ok"], results[oid].get("error"), label_of(c, oid))
            return jsonify(results=results, ok=all(r["ok"] for r in results.values()))
        for oid in ids:  # nacheinander, damit die PDU nicht überlastet wird
            lbl = label_of(c, oid)
            try:
                c.power(oid, action)
                results[oid] = {"ok": True}
                audit(c, oid, action, True, label=lbl)
            except RedfishError as e:
                results[oid] = {"ok": False, "error": str(e)}
                audit(c, oid, action, False, str(e), lbl)
        return jsonify(results=results, ok=all(r["ok"] for r in results.values()))

    @app.put("/api/pdus/<key>/outlets/<oid>")
    def outlet_label(key, oid):
        need_control()
        c = client(key)
        label = (require_json() or {}).get("label")
        if not isinstance(label, str) or len(label.strip()) > LABEL_MAX:
            abort(400, f"Beschreibung: höchstens {LABEL_MAX} Zeichen")
        label = label.strip()
        old = label_of(c, oid)
        c.set_label(oid, label)
        audit(c, oid, "Label", True, label=f"{old or ''} → {label}")
        return jsonify(ok=True)

    @app.get("/api/log")
    def action_log():
        key = request.args.get("pdu")
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()[-2000:]
        except OSError:
            lines = []
        out = []
        for ln in reversed(lines):
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if key and e.get("pdu") != key:
                continue
            out.append(e)
            if len(out) >= 100:
                break
        return jsonify(out)

    @app.get("/api/admin/diag/<key>")
    def diag(key):
        app.need_admin()
        c = client(key)
        data = {"app": VERSION, "pdu": key, "name": c.name, "ts": int(time.time()), "lastError": c.last_error, **c.diagnose()}
        resp = jsonify(data)
        resp.headers["Content-Disposition"] = f'attachment; filename="pdu-diagnose-{key}.json"'
        return resp

    @app.get("/api/admin/redfish/<key>")
    def raw(key):
        app.need_admin()
        c = client(key)
        path = request.args.get("path") or "/redfish/v1"
        if not re.match(r"^/redfish/v1(/[\w.\-$()=,%#]*)*(\?[\w.\-$()=,%&]*)?$", path) or ".." in path:
            abort(400, "Pfad muss mit /redfish/v1 beginnen")
        return jsonify(c.get(path))

    return app
