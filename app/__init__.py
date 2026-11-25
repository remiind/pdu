"""PDU – Monitoring und Socket-Steuerung für Rittal-PDUs über Redfish. Flask-App (SPA + JSON-API)."""
import concurrent.futures as cf
import fcntl
import json
import os
import re
import threading
import time

from flask import Flask, abort, g, jsonify, redirect, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import can_control, init_auth
from . import redfish
from .redfish import ACTIONS, PduClient, RedfishError

VERSION = "0.5.1"

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


GROUP_MAX = 32
# Gruppenfarben (ohne Türkis = „keine Gruppe“, ohne Rot/Grün = Status); jede Gruppe bekommt die nächste freie Farbe
GROUP_COLORS = ["#3b82f6", "#f59e0b", "#a855f7", "#ec4899", "#84cc16", "#6366f1", "#f97316", "#0ea5e9", "#d946ef", "#a16207",
                "#64748b", "#be185d"]
WATCH_S = 600                                           # so lange nach dem letzten Aufruf gilt eine PDU als „angesehen“
WARM_S = float(os.environ.get("PDU_REFRESH_ACTIVE", "5"))   # angesehene PDUs: so oft im Hintergrund neu lesen
IDLE_S = float(os.environ.get("PDU_REFRESH", "10"))         # alle anderen: dauerhaft so oft (Cache immer frisch)
RETRY_S = 30                                            # nicht erreichbare PDU: Pause bis zum nächsten Versuch


def create_app(data_dir=None, pdus=None, secure_cookies=None, background=None):
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
    groups_path = os.path.join(data_dir, "groups.json")
    settings_path = os.path.join(data_dir, "pdu-settings.json")

    # ---------- Einstellungen je PDU (z. B. Einbaulage) ----------
    def settings_load():
        try:
            with open(settings_path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def settings_set(key, patch):
        with open(settings_path + ".lock", "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            d = settings_load()
            d.setdefault(key, {}).update(patch)
            tmp = settings_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, settings_path)
            fcntl.flock(lk, fcntl.LOCK_UN)
    cache_dir = os.path.join(data_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    # ---------- Gruppen (in der App gespeichert: {pdu: {socket-id: gruppe}}) ----------
    def groups_raw():
        try:
            with open(groups_path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def groups_load():
        """{pdu-key: {socket-id: gruppe}} (ohne Verwaltungsfelder)."""
        return {k: v for k, v in groups_raw().items() if not k.startswith("_") and isinstance(v, dict)}

    def group_colors():
        cols = groups_raw().get("_colors")
        return cols if isinstance(cols, dict) else {}

    def groups_set(key, ids, group):
        with open(groups_path + ".lock", "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            d = groups_raw()
            per = d.setdefault(key, {})
            for oid in ids:
                if group:
                    per[oid] = group
                else:
                    per.pop(oid, None)
            if not per:
                d.pop(key, None)
            # Farben: nur für noch benutzte Gruppen behalten, neue Gruppe → erste freie Farbe
            used = {g for k, v in d.items() if not k.startswith("_") and isinstance(v, dict) for g in v.values()}
            cols = {g: c for g, c in (d.get("_colors") or {}).items() if g in used}
            for g in sorted(used - set(cols)):
                free = [c for c in GROUP_COLORS if c not in cols.values()]
                cols[g] = free[0] if free else GROUP_COLORS[len(cols) % len(GROUP_COLORS)]
            d["_colors"] = cols
            tmp = groups_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, groups_path)
            fcntl.flock(lk, fcntl.LOCK_UN)

    def decorate(snap):
        """Gruppen über die Momentaufnahme legen (Kopie, der Zwischenspeicher bleibt unverändert)."""
        all_groups = groups_load()
        mine = all_groups.get(snap["key"], {})
        out = dict(snap)
        out["outlets"] = [{**o, "group": mine.get(o["id"], "")} for o in snap["outlets"]]
        out["groups"] = sorted({g for per in all_groups.values() for g in per.values()}, key=str.lower)
        out["groupColors"] = group_colors()
        out["settings"] = {"orient": "desc", **settings_load().get(snap["key"], {})}  # desc = höchste Nummer oben
        out["age"] = round(max(0.0, time.time() - snap["ts"]), 1)
        return out

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
                 "outlet": outlet, "num": num_of(pdu, outlet), "label": label, "action": action, "ok": ok}
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

    def num_of(c, oid):
        o = next((x for x in ((c._snap or {}).get("outlets") or []) if x["id"] == str(oid)), None)
        return o.get("num") if o else None

    def label_of(c, oid):
        snap = c._snap
        o = next((x for x in (snap or {}).get("outlets", []) if x["id"] == str(oid)), None)
        return (o.get("label") or o.get("name")) if o else None

    # ---------- Hintergrund: Stände warm halten ----------
    watched = {}
    pending_refresh = {}

    def save_cache(c):
        snap = c._snap
        if not snap:
            return
        try:
            tmp = os.path.join(cache_dir, c.key + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, os.path.join(cache_dir, c.key + ".json"))
        except OSError:
            pass

    def norm_host(h):
        return re.sub(r"^https?://", "", str(h or "")).rstrip("/").lower()

    def migrate_keys():
        """PDU in PDU_LIST umbenannt → neuer Schlüssel. Alte Daten (Gruppen, Farben bleiben, Einbaulage, Stand,
        Protokoll) über die Adresse der PDU dem neuen Schlüssel zuordnen, damit nichts verloren geht."""
        by_host = {norm_host(c.host): k for k, c in clients.items()}
        moves = {}
        for fn in os.listdir(cache_dir):
            old = fn[:-5] if fn.endswith(".json") else None
            if not old or old in clients:
                continue
            try:
                with open(os.path.join(cache_dir, fn), "r", encoding="utf-8") as fh:
                    host = norm_host(json.load(fh).get("host"))
            except (OSError, ValueError):
                continue
            new = by_host.get(host)
            if new and not os.path.exists(os.path.join(cache_dir, new + ".json")):
                moves[old] = new
        if not moves:
            return
        for old, new in moves.items():
            src = os.path.join(cache_dir, old + ".json")
            try:
                with open(src, "r", encoding="utf-8") as fh:
                    snap = json.load(fh)
                snap["key"], snap["name"] = new, clients[new].name
                with open(os.path.join(cache_dir, new + ".json"), "w", encoding="utf-8") as fh:
                    json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))
                os.unlink(src)
            except (OSError, ValueError):
                pass
        for path in (groups_path, settings_path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                continue
            changed = False
            for old, new in moves.items():
                if old in d and new not in d:
                    d[new] = d.pop(old)
                    changed = True
            if changed:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(d, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            out = []
            for ln in lines:
                try:
                    e = json.loads(ln)
                except ValueError:
                    out.append(ln)
                    continue
                if e.get("pdu") in moves:
                    e["pdu"] = moves[e["pdu"]]
                    e["pduName"] = clients[e["pdu"]].name
                out.append(json.dumps(e, ensure_ascii=False) + "\n")
            tmp = log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(out)
            os.replace(tmp, log_path)
        except OSError:
            pass
        app.logger.warning("PDU-Schlüssel umgezogen: %s", moves)

    try:
        migrate_keys()
    except Exception:  # noqa: BLE001 – Übernahme darf den Start nie verhindern
        app.logger.exception("Übernahme alter PDU-Schlüssel fehlgeschlagen")

    for c in clients.values():  # gespeicherten Stand laden → erste Anzeige nach Neustart sofort
        c.from_disk = False
        try:
            with open(os.path.join(cache_dir, c.key + ".json"), "r", encoding="utf-8") as fh:
                c._snap = json.load(fh)
            c._snap_t = 0.0  # gilt als veraltet
            c.from_disk = True
        except (OSError, ValueError):
            pass

    def refresh_soon(key):
        pending_refresh[key] = True
        return True

    def refresher():
        busy = set()
        lock = threading.Lock()

        def run(c):
            try:
                c.snapshot(force=True)
                c.from_disk = False
                c.fail_t = 0.0
                save_cache(c)
            except Exception:  # noqa: BLE001 – Fehler stehen in c.last_error
                c.fail_t = time.time()
            finally:
                with lock:
                    busy.discard(c.key)

        with cf.ThreadPoolExecutor(max_workers=max(1, len(clients))) as ex:
            while True:
                now = time.time()
                for key, c in clients.items():
                    with lock:
                        if key in busy:
                            continue
                    snap = c._snap
                    age = now - snap["ts"] if snap and not c.from_disk else 1e9
                    hot = now - watched.get(key, 0) < WATCH_S
                    wanted = pending_refresh.pop(key, False)
                    if wanted or age >= (WARM_S if hot else IDLE_S):
                        if c.auth_block > now or (not wanted and now - getattr(c, "fail_t", 0) < RETRY_S):
                            continue
                        with lock:
                            busy.add(key)
                        ex.submit(run, c)
                time.sleep(1)

    if background is None:
        background = os.environ.get("PDU_BACKGROUND", "1") == "1"
    if background and clients:
        threading.Thread(target=refresher, name="pdu-refresher", daemon=True).start()

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
        watched[key] = time.time()
        force = request.args.get("force") == "1"
        snap = c._snap
        # Immer sofort den letzten Stand liefern – der Hintergrund hält ihn frisch. Direkt bei der PDU nachfragen
        # nur bei „Aktualisieren“ (force), direkt nach dem Schalten (Stand verworfen) oder ohne vorhandenen Stand.
        if not force and snap:
            age = time.time() - snap["ts"]
            invalidated = not c._snap_t and not c.from_disk
            if background:
                if invalidated or c.from_disk or age > IDLE_S * 2:
                    refresh_soon(key)
                if not invalidated:
                    return jsonify(decorate(snap))
            elif not invalidated and not c.from_disk and age < 15:
                return jsonify(decorate(snap))
        snap = c.snapshot(force=force)
        save_cache(c)
        return jsonify(decorate(snap))

    @app.put("/api/pdus/<key>/settings")
    def set_settings(key):
        need_control()
        client(key)
        body = require_json() or {}
        patch = {}
        if "orient" in body:
            if body["orient"] not in ("desc", "asc"):
                abort(400, "Einbaulage muss desc (höchste Nummer oben) oder asc sein")
            patch["orient"] = body["orient"]
        if not patch:
            abort(400, "Keine Einstellung angegeben")
        settings_set(key, patch)
        return jsonify(ok=True, settings={"orient": "desc", **settings_load().get(key, {})})

    @app.put("/api/pdus/<key>/groups")
    def set_groups(key):
        need_control()
        c = client(key)
        body = require_json() or {}
        ids, group = body.get("ids"), body.get("group", "")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 64 or not all(isinstance(i, str) for i in ids):
            abort(400, "Keine Sockets gewählt")
        if not isinstance(group, str) or len(group.strip()) > GROUP_MAX:
            abort(400, f"Gruppe: höchstens {GROUP_MAX} Zeichen")
        group = " ".join(group.split())
        snap = c._snap or c.snapshot()
        known = {o["id"] for o in snap["outlets"]}
        if not all(i in known for i in ids):
            abort(404, "Socket nicht gefunden")
        groups_set(key, ids, group)
        for oid in ids:
            audit(c, oid, "Group", True, label=group or "–")
        return jsonify(ok=True, group=group)

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
        refresh_soon(key)
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
        refresh_soon(key)
        return jsonify(results=results, ok=all(r["ok"] for r in results.values()))

    @app.put("/api/pdus/<key>/outlets/<oid>")
    def outlet_label(key, oid):
        need_control()
        c = client(key)
        label = (require_json() or {}).get("label")
        if not isinstance(label, str):
            abort(400, "Name fehlt")
        label = label.strip()
        if len(label) > 64:
            abort(400, "Name: höchstens 64 Zeichen")
        old = label_of(c, oid)
        try:
            name = c.set_label(oid, label)
        except RedfishError as e:
            audit(c, oid, "Label", False, str(e), label=f"{old or ''} → {label}")
            raise
        audit(c, oid, "Label", True, label=f"{old or ''} → {name}")
        return jsonify(ok=True, label=name)

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
