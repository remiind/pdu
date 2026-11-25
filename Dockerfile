# PDU – Monitoring und Steuerung für Rittal-PDUs (Redfish)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PDU_DATA=/data
WORKDIR /srv/pdu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY deploy/gunicorn.conf.py ./gunicorn.conf.py

# eigener Benutzer, Daten (Benutzer, Schaltprotokoll) liegen im Volume /data
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin pdu \
 && mkdir -p /data && chown pdu:pdu /data
USER pdu
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:create_app()"]
