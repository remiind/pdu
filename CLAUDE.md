# CLAUDE.md – PDU

Weboberfläche für Rittal-PDUs (7979310 = 10 Sockets, 7979340 = 42 Sockets, Firmware 10.2.1) über die Redfish-API: Monitoring + Socket-Steuerung. Docker-Web-App, Repo `remiind/pdu` (**public** → keine Passwörter/Zugangsdaten ins Repo, auch nicht in Tests). Sprache der Oberfläche und Doku: **Deutsch**.

## Projektregeln
- Diese Datei bei jeder relevanten Änderung aktuell halten; `README.md` bei Bedarf anpassen.
- Neue Features auf einem `feature/…`-Branch entwickeln, nach erfolgreichem Test in `main` mergen.
- Optik **nur** nach Design-Kit (caweb/RADSrv/Plan): `app/static/design.css` unverändert lassen, Ergänzungen in `app/static/pdu.css`.
- Bereitstellung als Docker-Image über GHCR (`.github/workflows/ci.yml`), keine ZIPs.
- Deploy-/Update-Anleitungen immer für beide Hosts: **tmplubuntu26** (`docker-compose.standalone.yml`, eigener Caddy auf Port 8443, Plan belegt 443) und **docksrv** (`docker-compose.yml`, Netzwerk `proxy` des vorhandenen Caddy).

## Aufbau
```
app/__init__.py   Flask-App: PDU-Liste aus PDU_LIST, JSON-API, Schaltprotokoll, Sicherheits-Header (CSP, HSTS), HTTPS-Pflicht
app/redfish.py    PduClient (Session-Token, Retry bei 401; lehnt die PDU die Session ab → Basic-Auth, `auth_mode`; Fehlermeldung nennt Code + Text der PDU für beide Wege; $expand oder parallele Einzelabrufe, Cache) + normalize()
app/auth.py       Benutzer/Login aus Plan übernommen (users.json, scrypt, Cookie pdu_session); Flag `control` (False = nur ansehen)
app/templates/index.html  SPA, lädt js/app.js
app/static/js/
  app.js       Hash-Router: #/monitor[/<key>], #/control[/<key>], #/explorer[/<key>] (Admin), #/users (Admin)
  shell.js     Seitenleiste, Farbschema, picker() = PDU-Auswahl mit Live-Leistung (/api/pdus/summary alle 10 s), every() = Polling (pausiert im Hintergrund)
  monitor.js   Kacheln, Phasen mit Auslastungsbalken, Leistungsverlauf (nur im Browser, HIST, max. 1 h), Sicherungen, Sensoren, Socket-Tabelle
  control.js   Socket-Kacheln (in-place aktualisiert, Gruppen nach Phase), Filter, Auswahl + Auswahlleiste, Bestätigung bei Aus/Neustart, Umbenennen, Protokoll
  explorer.js  Redfish-Rohdaten als Baum, Links auf @odata.id
  users.js · login.js · ui.js · util.js · icons.js · theme.js · api.js · session.js
tests/mock_redfish.py  simulierte PDU (Sessions, Mains mit PolyPhase*, Branches F1/F2, Outlets, PowerControl, PATCH UserLabel, optional $expand)
tests/test_api.py      Backend-Tests gegen zwei simulierte PDUs + eine nicht erreichbare
```

## API
- `GET /api/pdus` → `{pdus:[{key,name,group,host,error}], control}` · `GET /api/pdus/summary` → je PDU `{ok,power,model,health|error}` (nutzt Momentaufnahme < 15 s, sonst nur PDU + Metrics)
- `GET /api/pdus/<key>[?force=1]` → Momentaufnahme `{info, phases[], totals{power,energy,frequency,neutral,current}, env, branches[], outlets[], sensors[], counts}`
  - Outlet: `{id,num,name,label(UserLabel),type(C13/C19/…),state(PowerState),locked,phase(L1–L3 aus ElectricalContext oder Branch),branch,current,power,energy,voltage,rated,target,health,status}`
  - Phasen aus einem Mains-Kreis mit `PolyPhase*` **oder** aus je einem Mains-Kreis pro Phase.
- `POST /api/pdus/<key>/outlets/<id>/power {action:On|Off|PowerCycle}` · `POST /api/pdus/<key>/power {ids[],action}` (nacheinander) · `PUT /api/pdus/<key>/outlets/<id> {label}` → PATCH `UserLabel`
- `GET /api/log?pdu=<key>` (letzte 100) · `GET /api/admin/redfish/<key>?path=/redfish/v1/…` (nur GET, nur Admin)
- Schalten/Umbenennen braucht `control` (Admins immer). POST/PUT nur mit JSON (415).

## Konfiguration (.env)
`PDU_LIST` (`Gruppe/Name=Host; …`), `PDU_USER`, `PDU_PASSWORD`, `PDU_VERIFY_TLS` (0), `PDU_CACHE_SECONDS` (2), `PDU_HOST`/`PDU_TLS`/`PDU_HTTPS_PORT` (nur Standalone-Caddy). Gunicorn: 1 Worker (Sessions/Cache im Prozess), 16 Threads.

## Befehle
```bash
python tests/mock_redfish.py 9101 42 &                   # simulierte PDU
PDU_DATA=./data PDU_COOKIE_SECURE=0 PDU_USER=apiuser PDU_PASSWORD=mock-secret-123 PDU_LIST="Labor/Test=http://127.0.0.1:9101" flask --app "app:create_app" run -p 8098
pytest -q tests
for f in app/static/js/*.js; do node --check "$f"; done
docker compose -f docker-compose.standalone.yml up -d --build   # tmplubuntu26 (Port 8443)
docker compose up -d --build                                     # docksrv (Netzwerk proxy)
```

## Status / Ideen
- v0.1.1: Anmeldung: Rückfall auf Basic-Auth, wenn die PDU keine Session anlegt; ausführliche Fehlermeldung.
- v0.1.0: Monitoring, Steuerung, Redfish-Explorer, Benutzer mit Recht „Schalten“, Schaltprotokoll. Feldzuordnung nach DMTF-Schema; gegen echte Rittal-PDUs noch zu prüfen (Explorer nutzen, falls Werte fehlen).
- Ideen: PDUs in der Oberfläche verwalten statt .env, Verlauf serverseitig speichern, Schwellwerte/Alarme, Socket-Gruppen (z. B. „Teststack 1“ = mehrere Sockets über PDUs hinweg), Einschaltverzögerung.
