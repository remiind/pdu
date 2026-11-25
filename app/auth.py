"""Benutzer, Anmeldung (Flask-Session) und Benutzerverwaltung (übernommen aus Plan).

Benutzer liegen in $PDU_DATA/users.json (Passwörter nur als scrypt-Hash), der Session-Schlüssel in
$PDU_DATA/secret.key. Der erste angelegte Benutzer wird Admin. Benutzer mit `control=False`
dürfen nur ansehen, nicht schalten.
"""
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager

from flask import abort, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

NAME_RE = re.compile(r"^[\w.@-]{2,32}$")
MIN_PW = 8
MAX_PW = 200
PUBLIC_API = {"/api/auth/state", "/api/auth/login", "/api/auth/setup"}
DUMMY_HASH = generate_password_hash("pdu-dummy-password")

_fails = {}  # ip -> [Zeitstempel fehlgeschlagener Logins]


class UserStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "users.json")
        self.lockpath = os.path.join(data_dir, "users.lock")

    @contextmanager
    def lock(self):
        with open(self.lockpath, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh).get("users", [])
        except (OSError, ValueError):
            return []

    def save(self, users):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"users": users}, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, uid):
        return next((u for u in self.load() if u["id"] == uid), None)

    def by_name(self, name):
        n = (name or "").strip().lower()
        return next((u for u in self.load() if u["name"].lower() == n), None)


def pw_tag(user):
    """Ändert sich mit dem Passwort → alte Sitzungen werden ungültig."""
    return hashlib.sha256(user["hash"].encode()).hexdigest()[:16]


def secret_key(data_dir):
    p = os.path.join(data_dir, "secret.key")
    if not os.path.isfile(p):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    with open(p, "r") as fh:
        return fh.read().strip()


def public(u):
    return {"id": u["id"], "name": u["name"], "admin": bool(u.get("admin")), "control": can_control(u)}


def can_control(u):
    return bool(u) and (bool(u.get("admin")) or u.get("control", True) is not False)


def check_name(name):
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        abort(400, "Benutzername: 2–32 Zeichen (Buchstaben, Ziffern, . _ - @)")
    return name


def check_pw(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PW:
        abort(400, f"Passwort: mindestens {MIN_PW} Zeichen")
    if len(pw) > MAX_PW:
        abort(400, "Passwort ist zu lang")
    return pw


def init_auth(app, data_dir, require_json, secure_cookies=True):
    store = UserStore(data_dir)
    app.config.update(
        SECRET_KEY=secret_key(data_dir),
        SESSION_COOKIE_NAME="pdu_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_cookies,
        PERMANENT_SESSION_LIFETIME=14 * 24 * 3600,
    )
    app.users = store
    app.need_admin = lambda: need_admin()

    def login_as(u):
        session.clear()
        session.permanent = True
        session["uid"] = u["id"]
        session["tag"] = pw_tag(u)

    @app.before_request
    def authenticate():
        g.user = None
        uid = session.get("uid")
        if uid:
            u = store.get(uid)
            if u and hmac_eq(session.get("tag"), pw_tag(u)):
                g.user = u
        p = request.path
        if not p.startswith("/api/"):
            return None
        if request.method in ("POST", "PUT", "PATCH") and not request.is_json:
            abort(415)  # kein Formular-Post von fremden Seiten
        if p not in PUBLIC_API and g.user is None:
            return jsonify(error="Nicht angemeldet"), 401
        return None

    def hmac_eq(a, b):
        return isinstance(a, str) and secrets.compare_digest(a, b)

    def need_admin():
        if not g.user or not g.user.get("admin"):
            abort(403, "Nur für Administratoren")

    def throttle():
        ip = request.remote_addr or "?"
        now = time.time()
        recent = [t for t in _fails.get(ip, []) if now - t < 300]
        _fails[ip] = recent
        if len(recent) >= 10:
            abort(429, "Zu viele Fehlversuche – bitte kurz warten")
        return ip

    # ---------- Anmeldung ----------
    @app.get("/api/auth/state")
    def auth_state():
        return jsonify(setup=not store.load(), user=public(g.user) if g.user else None)

    @app.post("/api/auth/setup")
    def auth_setup():
        body = require_json() or {}
        name, pw = check_name(body.get("name")), check_pw(body.get("password"))
        with store.lock():
            if store.load():
                abort(409, "Es existiert bereits ein Benutzer")
            u = {"id": secrets.token_hex(6), "name": name, "hash": generate_password_hash(pw), "admin": True, "created": int(time.time())}
            store.save([u])
        login_as(u)
        return jsonify(user=public(u)), 201

    @app.post("/api/auth/login")
    def auth_login():
        body = require_json() or {}
        ip = throttle()
        u = store.by_name(body.get("name"))
        pw = str(body.get("password") or "")[:MAX_PW]
        ok = check_password_hash(u["hash"] if u else DUMMY_HASH, pw)
        if not (u and ok):
            _fails.setdefault(ip, []).append(time.time())
            abort(401, "Benutzername oder Passwort falsch")
        _fails.pop(ip, None)
        login_as(u)
        return jsonify(user=public(u))

    @app.post("/api/auth/logout")
    def auth_logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/auth/password")
    def auth_password():
        body = require_json() or {}
        if not check_password_hash(g.user["hash"], str(body.get("old") or "")[:MAX_PW]):
            abort(403, "Aktuelles Passwort ist falsch")
        new = check_pw(body.get("new"))
        with store.lock():
            users = store.load()
            me = next(u for u in users if u["id"] == g.user["id"])
            me["hash"] = generate_password_hash(new)
            store.save(users)
        login_as(me)
        return jsonify(ok=True)

    # ---------- Verwaltung (Admin) ----------
    @app.get("/api/admin/users")
    def admin_list():
        need_admin()
        return jsonify([{**public(u), "created": u.get("created", 0)} for u in store.load()])

    @app.post("/api/admin/users")
    def admin_create():
        need_admin()
        body = require_json() or {}
        name, pw = check_name(body.get("name")), check_pw(body.get("password"))
        with store.lock():
            users = store.load()
            if any(u["name"].lower() == name.lower() for u in users):
                abort(409, "Benutzername ist schon vergeben")
            u = {"id": secrets.token_hex(6), "name": name, "hash": generate_password_hash(pw), "admin": bool(body.get("admin")),
                 "control": body.get("control", True) is not False, "created": int(time.time())}
            users.append(u)
            store.save(users)
        return jsonify(public(u)), 201

    @app.put("/api/admin/users/<uid>")
    def admin_update(uid):
        need_admin()
        body = require_json() or {}
        with store.lock():
            users = store.load()
            u = next((x for x in users if x["id"] == uid), None)
            if not u:
                abort(404)
            if "password" in body:
                u["hash"] = generate_password_hash(check_pw(body["password"]))
            if "admin" in body:
                want = bool(body["admin"])
                if u.get("admin") and not want and sum(1 for x in users if x.get("admin")) <= 1:
                    abort(409, "Es muss mindestens ein Administrator bleiben")
                u["admin"] = want
            if "control" in body:
                u["control"] = bool(body["control"])
            store.save(users)
        return jsonify(public(u))

    @app.delete("/api/admin/users/<uid>")
    def admin_delete(uid):
        need_admin()
        if uid == g.user["id"]:
            abort(409, "Du kannst dich nicht selbst löschen")
        with store.lock():
            users = store.load()
            u = next((x for x in users if x["id"] == uid), None)
            if not u:
                abort(404)
            store.save([x for x in users if x["id"] != uid])
        return jsonify(ok=True)
