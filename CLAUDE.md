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
app/redfish.py    PduClient (Collections immer einzeln parallel laden – **kein $expand**, Rittal liefert dabei gekürzte Mitglieder; Session-Token, Retry bei 401; lehnt die PDU die Session ab → Basic-Auth, `auth_mode`; Fehlermeldung nennt Code + Text der PDU für beide Wege; nach Fehlschlag 300 s keine neuen Anmeldeversuche (`AUTH_BACKOFF`, schützt vor Account-Sperre); $expand oder parallele Einzelabrufe, Cache) + normalize()
app/auth.py       Benutzer/Login aus Plan übernommen (users.json, scrypt, Cookie pdu_session); Flag `control` (False = nur ansehen)
app/templates/index.html  SPA, lädt js/app.js
app/static/js/
  app.js       Hash-Router: #/monitor[/<key>], #/control[/<key>], #/explorer[/<key>] (Admin), #/users (Admin)
  shell.js     Seitenleiste, Farbschema, picker() = PDU-Auswahl mit Live-Leistung (/api/pdus/summary alle 10 s), every() = Polling (pausiert im Hintergrund)
  monitor.js   Kacheln, Phasen mit Auslastungsbalken, Leistungsverlauf (nur im Browser, HIST, max. 1 h), Sicherungen, Sensoren, Socket-Tabelle
  control.js   Socket-Kacheln (in-place aktualisiert, Gruppen nach Phase), Filter, Auswahl + Auswahlleiste, Bestätigung bei Aus/Neustart, Umbenennen, Protokoll
  explorer.js  Redfish-Rohdaten als Baum, Links auf @odata.id
  users.js · login.js · ui.js · util.js · icons.js · theme.js · api.js · session.js
tests/mock_redfish.py  make_pdu() = generische DMTF-PDU · make_rittal() = Nachbau der echten Rittal 7979340 (Diagnose-Paket 25.11.2026); CLI: `python tests/mock_redfish.py 9101 42 rittal`
tests/test_api.py      Backend-Tests gegen zwei simulierte PDUs + eine nicht erreichbare
```

## Rittal-Besonderheiten (FW 10.2.1, echte Daten)
- RackPDU-Id = Seriennummer (`/RackPDUs/43090021`), `Model` „PDU-MAN“, `PartNumber` 7979340, `Description` „PDU-SWI“ → Anzeige „7979340 PDU-SWI“; Placement-Platzhalter „Rack“/„Location“ werden ausgeblendet.
- Outlet-IDs `L<Phase>_<Nr>` (z. B. `L3_34`), Collection unsortiert → nach Nummer sortiert. Phase aus `ElectricalContext`.
- Messwerte als Excerpt `{DataSourceUri, Reading}`; Sensoren liegen unter `/redfish/v1/Chassis/PDU_<SN>/Sensors` (`/RackPDUs/<id>/Sensors` → 405). Gelesen werden nur Sensoren nach `SENSOR_RE` (Energy_L1..3, Frequency…, Current_N…, Temperatur/Feuchte), Liste 10 min gecacht.
- Mains AC1: `PolyPhaseCurrentAmps` mit Schlüsseln `Line1..3`, Spannung/Leistung `LineXToNeutral`; `EnergykWh` zeigt fälschlich auf Energy_L3; `Metrics.EnergykWh` = 10.194.415 (falsche Einheit) → Gesamtenergie = Summe Energy_L1..3.
- Sockets ohne eigene Messung (CurrentAmps 0,0, kein PowerWatts) → `metering:false`, Strom/Leistung je Socket = null, Spalten werden ausgeblendet.
- `Outlet.PowerControl` erlaubt nur On/Off → Neustart wird nachgebildet: einzeln Aus → `CYCLE_PAUSE` (5 s) → An; Auswahl: alle aus, warten, alle an.
- Branches L1–L3 entsprechen den Phasen → Karte „Sicherungen“ wird dann ausgeblendet.
- **Umbenennen:** PATCH auf `UserLabel` liefert immer 400 `ErrorValidating` (auch „Test“, jeder Socket; PATCH auf die PDU selbst: `key 'EquipmentType' not found`). `ElectricalConsumerNames` ist beschreibbar → `set_label()` probiert UserLabel und fällt auf `ElectricalConsumerNames:[name]` zurück (leer = `[]`), `label_field` merkt sich das. Anzeige: `label` = ElectricalConsumerNames[0] oder UserLabel, `pduLabel` = UserLabel.
- Account braucht Redfish-Rechte (`apiuser` ohne Rechte → 401 auf Session und Basic).

## API
- `GET /api/pdus` → `{pdus:[{key,name,group,host,error}], control}` · `GET /api/pdus/summary` → je PDU `{ok,power,model,health|error}` (nutzt Momentaufnahme < 15 s, sonst nur PDU + Metrics)
- `GET /api/pdus/<key>[?force=1]` → Momentaufnahme `{info, phases[], totals{power,energy,frequency,neutral,current}, env, branches[], outlets[], sensors[], counts}`
  - Outlet: `{id,num,name,label(ElectricalConsumerNames[0] oder UserLabel),pduLabel(UserLabel),type(C13/C19/…),state(PowerState),locked,phase(L1–L3 aus ElectricalContext oder Branch),branch,current,power,energy,voltage,rated,target,health,status}`
  - Phasen aus einem Mains-Kreis mit `PolyPhase*` **oder** aus je einem Mains-Kreis pro Phase.
- `POST /api/pdus/<key>/outlets/<id>/power {action:On|Off|PowerCycle}` · `POST /api/pdus/<key>/power {ids[],action}` (nacheinander) · `PUT /api/pdus/<key>/outlets/<id> {label}` → PATCH `UserLabel`, bei 400 `ElectricalConsumerNames`
- `GET /api/admin/diag/<key>` = Diagnose-Paket (Rohdaten PDU, Mains, Branches, 3 Outlets, Sensoren, DataSourceUri-Verweise, $expand-Test, Zeiten; Knopf im Redfish-Explorer)
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
- v0.2.3: Nach dem Umbenennen wird nachgelesen – speichert die PDU nicht, kommt eine klare Fehlermeldung statt „OK“; Protokoll zeigt Socket-Nummer statt ID.
- v0.2.2: Umbenennen über `ElectricalConsumerNames` (Rittal lehnt `UserLabel` ab); Tooltip zeigt die Beschreibung an der PDU.
- v0.2.1: Lesbare Fehlermeldungen der PDU (ExtendedInfo als Liste oder JSON-Text in „message“), Hinweis bei abgelehnter Beschreibung.
- v0.2.0: Anpassung an echte Rittal-Daten (siehe „Rittal-Besonderheiten“): kein $expand, Chassis-Sensoren, Energie je Phase, Neustart-Nachbildung, Sockets ohne Messung, Kachel „Höchste Phasenlast“.
- v0.1.3: Diagnose-Paket im Redfish-Explorer; Kurzstatus der PDU-Auswahl fängt alle Fehler je PDU ab.
- v0.1.2: Nach fehlgeschlagener Anmeldung 5 min Pause je PDU (keine Account-Sperre durch Dauer-Fehlversuche).
- v0.1.1: Anmeldung: Rückfall auf Basic-Auth, wenn die PDU keine Session anlegt; ausführliche Fehlermeldung.
- v0.1.0: Monitoring, Steuerung, Redfish-Explorer, Benutzer mit Recht „Schalten“, Schaltprotokoll. Feldzuordnung nach DMTF-Schema; gegen echte Rittal-PDUs noch zu prüfen (Explorer nutzen, falls Werte fehlen).
- Ideen: PDUs in der Oberfläche verwalten statt .env, Verlauf serverseitig speichern, Schwellwerte/Alarme, Socket-Gruppen (z. B. „Teststack 1“ = mehrere Sockets über PDUs hinweg), Einschaltverzögerung.
