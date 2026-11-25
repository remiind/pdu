# PDU

Moderne Weboberfläche für **Rittal-PDUs** (getestet für 7979310 / 7979340, Firmware 10.2.1) über die **Redfish-REST-API** – als Ersatz für die eingebaute Weboberfläche.

- **Monitoring**: Gesamtleistung, Energie, Phasen L1–L3 (Spannung, Strom, Auslastung, Leistung, Energie), Neutralleiter, Frequenz, Sicherungen, Verbrauch je Socket (sortier- und durchsuchbar), Leistungsverlauf live. Aktualisierung alle 5 Sekunden.
- **Steuerung**: Sockets als Kacheln mit Schalter, gruppiert nach Phase, Filter (An/Aus/Phase/Suche), Mehrfachauswahl (Klick, Umschalt = Bereich) mit Einschalten / Ausschalten / Neu starten, Beschreibung ändern, Protokoll der Schaltvorgänge (wer, wann, was).
- **PDU-Auswahl** oben auf jeder Seite, gruppiert nach Standort, mit Live-Leistung und Online-Status.
- **Redfish-Explorer** (Admin): Rohdaten der PDU lesen und durch die `@odata.id`-Links klicken.
- **Benutzer** wie in Plan: erster Login = Administrator; Benutzer können auf „nur ansehen“ gesetzt werden.
- Hell / Dunkel / Auto, Design-Kit von caweb/RADSrv/Plan.

## Einrichtung

```bash
git clone https://github.com/remiind/pdu.git && cd pdu
cp .env.example .env
nano .env      # PDU_HOST, PDU_LIST, PDU_PASSWORD eintragen
```

`PDU_LIST` enthält die PDUs als `Gruppe/Name=Host`, getrennt durch `;` (die Gruppe ist optional und erscheint als Überschrift in der Auswahl). Benutzer und Passwort (`PDU_USER`, `PDU_PASSWORD`) gelten für alle PDUs. Die `.env` bleibt nur auf dem Host und gehört **nicht** ins Repo.

### Variante A – eigenständig mit eigenem Caddy (z. B. tmplubuntu26 neben Plan)

Läuft auf **Port 8443**, damit Plan seinen Port 443 behält.

```bash
echo 'COMPOSE_FILE=docker-compose.standalone.yml' >> .env   # einmalig, dann reicht „docker compose …“
sudo docker compose up -d --build
```

Aufruf: `https://<PDU_HOST>:8443` – das Zertifikat der Caddy-eigenen CA einmal im Browser bestätigen.

### Variante B – hinter vorhandenem Caddy (docksrv, Netzwerk `proxy`)

```bash
sudo mkdir -p /opt/stacks/pdu/data && sudo chown 10001:10001 /opt/stacks/pdu/data && sudo chmod 700 /opt/stacks/pdu/data
sudo docker compose up -d --build
```

Caddyfile-Eintrag im vorhandenen Caddy:

```
pdu.example.de {
	tls /certs/<zertifikat>.crt /certs/<schlüssel>.key
	reverse_proxy pdu:8080
}
```

### Update

```bash
git pull && sudo docker compose up -d --build
```

## Technik

- Flask + Gunicorn (1 Prozess, 16 Threads), Python 3.13, keine externen Ressourcen (CSP `'self'`).
- Redfish: Anmeldung per Session (`X-Auth-Token`, Rückfall auf Basic-Auth), Daten unter `/redfish/v1/PowerEquipment/RackPDUs/<id>` (`Mains`, `Branches`, `Outlets`, `Metrics`, `Sensors`), Schalten über `Outlet.PowerControl` (`On`, `Off`), Beschreibung per `PATCH` auf `UserLabel`. Collections werden parallel einzeln geladen (Rittal liefert bei `$expand` unvollständige Daten), Zusatzwerte (Energie je Phase, Frequenz) aus den Chassis-Sensoren. Kennt die PDU kein `PowerCycle`, wird der Neustart als Aus → 5 s → An nachgebildet. Messwerte werden 2 s zwischengespeichert.
- Daten (`$PDU_DATA`, Standard `/data`): `users.json` (Passwörter nur als scrypt-Hash), `secret.key`, `actions.jsonl` (Schaltprotokoll).

## Entwicklung

```bash
pip install -r requirements.txt pytest
python tests/mock_redfish.py 9101 42 &          # simulierte PDU mit 42 Sockets
PDU_DATA=./data PDU_COOKIE_SECURE=0 PDU_USER=apiuser PDU_PASSWORD=mock-secret-123 \
  PDU_LIST="Labor/Test=http://127.0.0.1:9101" flask --app "app:create_app" run -p 8098
pytest -q tests
```

Lizenz: GPL-3.0
