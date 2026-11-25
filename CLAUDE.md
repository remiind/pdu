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
app/__init__.py   Flask-App: PDU-Liste aus PDU_LIST, JSON-API, Schaltprotokoll, Gruppen (groups.json), Hintergrund-Aktualisierung + Stand-Cache (cache/<pdu>.json), Sicherheits-Header (CSP, HSTS), HTTPS-Pflicht
app/rittalweb.py  RittalWeb: Login + setValues an der Rittal-Weboberfläche, nur zum Umbenennen
app/redfish.py    PduClient (Collections immer einzeln parallel laden – **kein $expand**, Rittal liefert dabei gekürzte Mitglieder; Session-Token, Retry bei 401; lehnt die PDU die Session ab → Basic-Auth, `auth_mode`; Fehlermeldung nennt Code + Text der PDU für beide Wege; nach Fehlschlag 300 s keine neuen Anmeldeversuche (`AUTH_BACKOFF`, schützt vor Account-Sperre); $expand oder parallele Einzelabrufe, Cache) + normalize()
app/auth.py       Benutzer/Login aus Plan übernommen (users.json, scrypt, Cookie pdu_session); Flag `control` (False = nur ansehen)
app/templates/index.html  SPA, lädt js/app.js
app/static/js/
  app.js       Hash-Router: #/monitor[/<key>], #/control[/<key>] (Standard, auch nach dem Abmelden), #/explorer[/<key>] (Admin), #/users (Admin)
  shell.js     Seitenleiste, Farbschema, picker() = PDU-Auswahl mit Live-Leistung (/api/pdus/summary alle 10 s), every() = Polling (pausiert im Hintergrund)
  monitor.js   Kacheln, Phasen mit Auslastungsbalken, Leistungsverlauf (nur im Browser, HIST, max. 1 h), Sicherungen, Sensoren, Socket-Tabelle
  control.js   kompakte Socket-Kacheln (2 Zeilen, Name .75rem → 20 Zeichen passen, 7 Spalten bei 1920 px, `.cmain` max. 1720 px; Balkenfarbe = Gruppe; in-place aktualisiert), Ansicht **Einbau** (Standard)/Phase/Gruppe/Liste (`prefs.view`, localStorage `pdu.control2`), Filter + Suche (auch Gruppe), Auswahl + Auswahlleiste (An/Aus/Neustart/Gruppe …), Bearbeiten-Dialog (Name auf der PDU + Gruppe in der App, Doppelklick), Protokoll
  icons.js     ico() Linien-Icons · plugIco(type) = Buchsen-Icon (Blick auf die Buchse) für C13/C14/C15, C19/C20/C21, Schuko – im Typ-Chip (.tchip, .pico) in Steuerung und Monitoring
  explorer.js  Redfish-Rohdaten als Baum, Links auf @odata.id
  users.js · login.js · ui.js · util.js · theme.js · api.js · session.js
tests/mock_redfish.py  make_pdu() = generische DMTF-PDU · make_rittal() = Nachbau der echten Rittal 7979340 inkl. /cgi-bin/json.cgi (Diagnose-Paket + rittal_probe 25.11.2026); CLI: `python tests/mock_redfish.py 9101 42 rittal`
tools/rittal_probe.py  liest /cgi-bin/json.cgi der Rittal-Weboberfläche aus (createSession, getDeviceDescriptions …), nur lesend, Ausgabe ohne Passwort/Token
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
- **Umbenennen:** Redfish speichert nichts – PATCH `UserLabel` → 400 `ErrorValidating`, `ElectricalConsumerNames` → 200, wird aber verworfen. Lösung: `set_label()` probiert einmal PATCH `UserLabel` (andere Hersteller), merkt sich bei 400 `label_via="web"` und schreibt über `app/rittalweb.py` (json.cgi `setValues`). Danach wird `UserLabel` per Redfish nachgelesen (bis 3 s), sonst Fehler 409. Leer = „Socket NN“.
- **Weboberfläche** (`app/rittalweb.py`): `POST /cgi-bin/json.cgi`, form-urlencoded, Rumpf `befehl={json}` unkodiert. Login `createSession={"username","password","force":0}` → `{"result":0}` + Cookie `sessionId` (Auto-Logout 1800 s → App meldet nach 1500 s neu an, bei `result≠0` einmal neu). `getDeviceInfos` (deviceIndex ↔ serialNumber; Gerät 2 = PDU-SWI mit Redfish-SN), `getDeviceDescriptions` (Variablen-Index = Position in `descriptions`, leere `{}` zählen mit). Socket-Name = `Sockets.Socket NN.General.DescName` (= Redfish `UserLabel`), Regeln `maxLen 20`, `^([-_ a-zA-Z0-9]{0,20})$`. Setzen (wie Browser: `looseFocus={"focus":1}` → `setValues` → `looseFocus={"focus":0}`, am 25.11. mit `rittal_probe --set` auf der echten PDU bestätigt): `setValues={"variables":[{"deviceIndex":2,"variableIndex":139,"value":"…"}]}` (Socket 02 → 139). Zuordnung 1 h gecacht.
- Account braucht Redfish-Rechte (`apiuser` ohne Rechte → 401 auf Session und Basic).

## Ansicht „Einbau“
- Die 42er-PDUs hängen mit **Socket 42 oben, 01 unten** (oben L3 42–29, darunter Controller, L2 28–15, unten L1 14–01; je Phase 2× C19 + 12× C13 in Blöcken). Ansicht `rack` in control.js: je Phase eine Spalte (`.rackgrid`/`.rackcol`), Spalten und Sockets in Einbaureihenfolge – `orient` `desc` (höchste Nummer oben → Spalten L3|L2|L1, absteigend) oder `asc` (umgekehrt). Gestrichelte Lücke (`.rgap`), wo der Socket-Typ wechselt. Kacheln dort einzeilig (gleiche Elemente, nur CSS: `.rackcol .srow { display: contents }` + `order`); Reihenfolge Auswahl · Nr · **Typ** (feste Breite, damit C13/C19 untereinander stehen) · Name · Gruppe · … · Schalter.
- Einbaulage gilt je PDU für alle Benutzer: `$PDU_DATA/pdu-settings.json` `{pdu-key: {orient}}`, `PUT /api/pdus/<key>/settings {orient: desc|asc}` (braucht `control`), Momentaufnahme liefert `settings` (Standard `desc`). Knopf „42 oben ⇅“ in der Werkzeugleiste (nur in „Einbau“).

## Gruppen & Tempo
- **Gruppen** gehören der App (die PDU kennt nur numerische Gruppen 0–99): `$PDU_DATA/groups.json` `{pdu-key: {socket-id: name}, _colors: {name: hex}}`, max. 32 Zeichen. **Farben:** jede neue Gruppe bekommt die erste freie Farbe aus `GROUP_COLORS` (12, ohne Türkis/Rot/Grün), stabil; ungenutzte werden freigegeben. Kachel-Balken links = Gruppenfarbe (`--gc`), ohne Gruppe Türkis (`--accent`), ausgeschaltet blass. Frontend: `setGroupColors()`/`groupColor()` in util.js. `decorate()` legt `group` je Outlet und `groups` (alle Namen über alle PDUs, für Vorschläge) über die Momentaufnahme – nie in den Cache.
- **Robustheit:** `collection()` lädt mit max. 4 parallelen Abrufen (PDU arbeitet nacheinander); fällt ein einzelnes Mitglied aus, wird sein letzter Rohstand (`_raw`) genommen, fehlen dann noch welche → Fehler. Ein Stand ohne Sockets ersetzt nie einen guten (Fehler, letzter Stand bleibt) – vorher führte ein einzelner Timeout zu „0 Sockets“ beim PDU-Wechsel. Phasen fehlen kurz → aus dem letzten Stand.
- **Tempo:** Eine Rittal-Momentaufnahme braucht ~50 Redfish-Abrufe à ~0,07 s (kein `$expand`), die PDU bedient sie nacheinander → 3–4 s. Deshalb liest ein Hintergrund-Thread (`refresher`, `PDU_BACKGROUND=1`) **alle PDUs dauerhaft**: angesehene (`WATCH_S`=600 s nach letztem Aufruf) alle `PDU_REFRESH_ACTIVE`=5 s, alle anderen alle `PDU_REFRESH`=10 s (Abstand zählt ab Ende des letzten Abrufs → PDU ist ~25–40 % der Zeit beschäftigt). Nicht erreichbare PDUs: Pause `RETRY_S`=30 s. `GET /api/pdus/<key>` liefert **immer sofort** den letzten Stand (stößt bei Bedarf einen Abruf an); direkt gelesen wird nur bei `?force=1`, direkt nach dem Schalten (Stand verworfen) oder ohne vorhandenen Stand. Mock mit Trägheit: `python tests/mock_redfish.py 9101 42 rittal 0.07` (direkter Abruf ~8 s, mit Cache 0,06 s). Jeder Stand wird nach `$PDU_DATA/cache/<key>.json` geschrieben und beim Start geladen (`from_disk`) → erste Anzeige nach Neustart sofort, Feld `age` zeigt das Alter, die Oberfläche holt dann gleich den frischen Stand. Tests laufen ohne Hintergrund (`tests/conftest.py`).

## API
- `GET /api/pdus` → `{pdus:[{key,name,group,host,error}], control}` · `GET /api/pdus/summary` → je PDU `{ok,power,model,health|error}` (nutzt Momentaufnahme < 15 s, sonst nur PDU + Metrics)
- `GET /api/pdus/<key>[?force=1]` → Momentaufnahme `{info, phases[], totals{power,energy,frequency,neutral,current}, env, branches[], outlets[], sensors[], counts, groups[], groupColors{}, settings{orient}, age}`
  - Outlet: `{id,num,name,label(UserLabel),group(App),type(C13/C19/…),state(PowerState),locked,phase(L1–L3 aus ElectricalContext oder Branch),branch,current,power,energy,voltage,rated,target,health,status}`
  - Phasen aus einem Mains-Kreis mit `PolyPhase*` **oder** aus je einem Mains-Kreis pro Phase.
- `PUT /api/pdus/<key>/settings {orient}` → Einbaulage je PDU (`desc` = höchste Nummer oben, Standard; `asc`)
- `PUT /api/pdus/<key>/groups {ids[],group}` → Gruppe setzen/entfernen (leer), Protokoll-Aktion `Group`
- `POST /api/pdus/<key>/outlets/<id>/power {action:On|Off|PowerCycle}` · `POST /api/pdus/<key>/power {ids[],action}` (nacheinander) · `PUT /api/pdus/<key>/outlets/<id> {label}` → `{ok,label}` (Redfish `UserLabel`, bei Rittal über json.cgi; max. 64, Rittal 20 Zeichen)
- `GET /api/admin/diag/<key>` = Diagnose-Paket (Rohdaten PDU, Mains, Branches, 3 Outlets, Sensoren, DataSourceUri-Verweise, $expand-Test, Zeiten; Knopf im Redfish-Explorer)
- `GET /api/log?pdu=<key>` (letzte 100) · `GET /api/admin/redfish/<key>?path=/redfish/v1/…` (nur GET, nur Admin)
- Schalten/Umbenennen braucht `control` (Admins immer). POST/PUT nur mit JSON (415).

## Konfiguration (.env)
`PDU_LIST` (`Gruppe/Name=Host; …`; Schlüssel = slug(Name), z. B. `lab-da-pdu001` – Umbenennen erlaubt: `migrate_keys()` ordnet beim Start alte Schlüssel über die Host-Adresse aus `cache/<alt>.json` dem neuen zu und zieht groups.json, pdu-settings.json, Cache und actions.jsonl mit um), `PDU_USER`, `PDU_PASSWORD`, `PDU_VERIFY_TLS` (0), `PDU_CACHE_SECONDS` (2), `PDU_BACKGROUND` (1), `PDU_REFRESH` (10 s), `PDU_REFRESH_ACTIVE` (5 s), `PDU_HOST`/`PDU_TLS`/`PDU_HTTPS_PORT` (nur Standalone-Caddy). Gunicorn: 1 Worker (Sessions/Cache im Prozess), 16 Threads.

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
- v0.5.3: „Steuerung“ ist die Startseite (ohne bzw. mit unbekanntem Hash und nach dem Abmelden).
- v0.5.2: Socket-Typ mit Buchsen-Icon (SVG, C13/C19/Schuko); in der Ansicht „Einbau“ steht der Typ direkt hinter der Nummer → C13/C19 bündig untereinander, auch wenn eine Gruppe angezeigt wird.
- v0.5.1: Untertitel „Rittal · Redfish“ und Hilfezeile der Steuerung entfernt; PDU-Namen in PDU_LIST umbenennbar ohne Datenverlust (Übernahme über die Adresse).
- v0.5.0: Ansicht „Einbau“ (Standard): je Phase eine Spalte, Sockets wie montiert (42 oben … 01 unten), Lücken zwischen den Socket-Blöcken, einzeilige Kacheln; Einbaulage je PDU umschaltbar und für alle gespeichert.
- v0.4.2: Kein „0 Sockets“ mehr nach PDU-Wechsel (einzelne Ausfälle abgefangen, halbe Stände verworfen, weniger parallele Abrufe); Gruppenfarben am Kachelrand (Standard Türkis, jede Gruppe eigene, stabile Farbe); kleinere Namen (20 Zeichen lesbar), breitere Steuerungsseite.
- v0.4.1: Alle PDUs werden dauerhaft alle 10 s (angesehene alle 5 s) im Hintergrund gelesen; Seiten zeigen immer sofort den letzten Stand; Pause bei nicht erreichbaren PDUs; Mock mit realistischer Trägheit.
- v0.4.0: Kompakte Kacheln (alle 42 Sockets auf einem Bildschirm), Socket-Gruppen (Dialog, Auswahl, Ansicht „Gruppe“, Suche, Monitoring-Spalte), Hintergrund-Aktualisierung + gespeicherter Stand → Steuerung/Monitoring erscheinen sofort.
- v0.3.1: Umbenennen schickt wie der Browser `looseFocus` vor/nach `setValues` (Schreibtest auf echter PDU erfolgreich).
- v0.3.0: Umbenennen funktioniert auf Rittal: über die interne json.cgi-Schnittstelle der Weboberfläche (DescName), Prüfung der Rittal-Regeln im Dialog, Nachlesen per Redfish; ElectricalConsumerNames-Umweg entfernt. Mock `make_rittal` kann json.cgi.
- v0.2.3: Nach dem Umbenennen wird nachgelesen – speichert die PDU nicht, kommt eine klare Fehlermeldung statt „OK“; Protokoll zeigt Socket-Nummer statt ID.
- v0.2.2: Umbenennen über `ElectricalConsumerNames` (Rittal lehnt `UserLabel` ab); Tooltip zeigt die Beschreibung an der PDU.
- v0.2.1: Lesbare Fehlermeldungen der PDU (ExtendedInfo als Liste oder JSON-Text in „message“), Hinweis bei abgelehnter Beschreibung.
- v0.2.0: Anpassung an echte Rittal-Daten (siehe „Rittal-Besonderheiten“): kein $expand, Chassis-Sensoren, Energie je Phase, Neustart-Nachbildung, Sockets ohne Messung, Kachel „Höchste Phasenlast“.
- v0.1.3: Diagnose-Paket im Redfish-Explorer; Kurzstatus der PDU-Auswahl fängt alle Fehler je PDU ab.
- v0.1.2: Nach fehlgeschlagener Anmeldung 5 min Pause je PDU (keine Account-Sperre durch Dauer-Fehlversuche).
- v0.1.1: Anmeldung: Rückfall auf Basic-Auth, wenn die PDU keine Session anlegt; ausführliche Fehlermeldung.
- v0.1.0: Monitoring, Steuerung, Redfish-Explorer, Benutzer mit Recht „Schalten“, Schaltprotokoll. Feldzuordnung nach DMTF-Schema; gegen echte Rittal-PDUs noch zu prüfen (Explorer nutzen, falls Werte fehlen).
- Ideen: PDUs in der Oberfläche verwalten statt .env, Verlauf serverseitig speichern, Schwellwerte/Alarme, PDU-übergreifende Gruppen-Ansicht (z. B. Teststack mit Netzteilen auf zwei PDUs) inkl. Gruppe schalten, Einschaltverzögerung.
