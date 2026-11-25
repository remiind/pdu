import os

bind = f"0.0.0.0:{os.environ.get('PDU_PORT', '8080')}"
# Ein Prozess mit vielen Threads: Redfish-Sessions und Zwischenspeicher liegen im Speicher dieses Prozesses,
# die Threads warten überwiegend auf die PDUs.
workers = 1
threads = int(os.environ.get("PDU_THREADS", "16"))
timeout = 90
accesslog = "-"
errorlog = "-"
