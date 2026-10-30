"""
MedFlow AG – Cold-Chain Metrics Exporter (Simulation)
=====================================================
Simuliert realistische Metriken für das Cold-Chain-Monitoring:
- Temperatur pro Fahrzeug/Sensor (2-8°C normal, ein Fahrzeug mit Kühlstörung)
- Request-Latenz (Order Service, Cold-Chain Service, ...)
- Error Rate
- Throughput (Requests/s, Messages/s)
- System-Metriken (CPU, Memory pro Service)

Port 8000: /metrics für Prometheus
Port 8001: Steuerseite für Störungen (Chaos-Schalter)
"""

import os
import time
import random
import math
import threading
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote_plus
from prometheus_client import (
    Gauge,
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ============================================================
# Konfiguration
# ============================================================
VEHICLE_COUNT = int(os.environ.get("VEHICLE_COUNT", 45))
ANOMALY_ENABLED = os.environ.get("ANOMALY_ENABLED", "true").lower() == "true"
ANOMALY_START_SECONDS = float(os.environ.get("ANOMALY_START_SECONDS", 300))
ANOMALY_RISE_PER_MINUTE = float(os.environ.get("ANOMALY_RISE_PER_MINUTE", 0.6))
ANOMALY_MAX_RISE = float(os.environ.get("ANOMALY_MAX_RISE", 8.0))
ANOMALY_VEHICLE = os.environ.get("ANOMALY_VEHICLE", "").strip()
DEFAULT_ERROR_RATE = float(os.environ.get("ERROR_RATE", 0.02))
PORT = 8000
CONTROL_PORT = 8001

START_TIME = time.time()

# ============================================================
# Metrik-Definitionen
# ============================================================

# --- Temperatur-Metriken ---
temperature_current = Gauge(
    "medflow_coldchain_temperature_celsius",
    "Aktuelle Temperatur im Transportfahrzeug",
    ["vehicle_id", "sensor_id", "zone", "route"],
)

temperature_threshold_upper = Gauge(
    "medflow_coldchain_temperature_threshold_upper_celsius",
    "Oberer Temperatur-Schwellwert",
    ["vehicle_id"],
)

temperature_threshold_lower = Gauge(
    "medflow_coldchain_temperature_threshold_lower_celsius",
    "Unterer Temperatur-Schwellwert",
    ["vehicle_id"],
)

# --- Abweichungs-Metriken ---
deviation_total = Counter(
    "medflow_coldchain_deviations_total",
    "Anzahl Temperaturabweichungen",
    ["vehicle_id", "severity", "zone"],
)

# --- Request-Metriken (Golden Signals) ---
http_requests_total = Counter(
    "medflow_http_requests_total",
    "Gesamtanzahl HTTP-Requests",
    ["service", "method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "medflow_http_request_duration_seconds",
    "HTTP Request-Dauer in Sekunden",
    ["service", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# --- MQTT/Event-Metriken ---
mqtt_messages_received_total = Counter(
    "medflow_mqtt_messages_received_total",
    "Empfangene MQTT-Nachrichten",
    ["topic", "qos"],
)

mqtt_messages_processed_total = Counter(
    "medflow_mqtt_messages_processed_total",
    "Verarbeitete MQTT-Nachrichten",
    ["topic", "status"],
)

event_processing_duration_seconds = Histogram(
    "medflow_event_processing_duration_seconds",
    "Event-Verarbeitungsdauer",
    ["event_type"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

# --- System-Metriken ---
service_cpu_usage = Gauge(
    "medflow_service_cpu_usage_percent",
    "CPU-Auslastung pro Service",
    ["service", "pod"],
)

service_memory_usage_bytes = Gauge(
    "medflow_service_memory_usage_bytes",
    "Memory-Nutzung pro Service",
    ["service", "pod"],
)

# --- Verfügbarkeits-Metriken ---
service_up = Gauge(
    "medflow_service_up",
    "Service-Verfügbarkeit (1=up, 0=down)",
    ["service"],
)

# --- Geschäfts-Metriken ---
orders_created_total = Counter(
    "medflow_orders_created_total",
    "Erstellte Bestellungen",
    ["customer_type", "priority"],
)

active_transports = Gauge(
    "medflow_active_transports",
    "Aktive Transporte",
    ["zone"],
)

# ============================================================
# Simulation
# ============================================================

# Fahrzeug-Konfiguration
ZONES = ["bern", "zurich", "lausanne"]
ROUTES = ["hospital-route", "pharmacy-route", "wholesaler-route"]

vehicles = []
for i in range(VEHICLE_COUNT):
    zone = ZONES[i % len(ZONES)]
    route = ROUTES[i % len(ROUTES)]
    vehicles.append(
        {
            "id": f"VH-{i+1:03d}",
            "zone": zone,
            "route": route,
            "base_temp": random.uniform(4.0, 6.0),  # Normaltemperatur
            "sensor_a_offset": random.uniform(-0.5, 0.5),
            "sensor_b_offset": random.uniform(-0.5, 0.5),
        }
    )
    # Schwellwerte setzen
    temperature_threshold_upper.labels(vehicle_id=f"VH-{i+1:03d}").set(8.0)
    temperature_threshold_lower.labels(vehicle_id=f"VH-{i+1:03d}").set(2.0)

VEHICLE_IDS = [v["id"] for v in vehicles]

# Services
SERVICES = ["order-service", "cold-chain-service", "notification-service", "warehouse-adapter"]

# ============================================================
# Laufzeit-Zustand (über die Steuerseite änderbar)
# ============================================================
INITIAL_ANOMALY_VEHICLE = ANOMALY_VEHICLE if ANOMALY_VEHICLE in VEHICLE_IDS else random.choice(VEHICLE_IDS)

state_lock = threading.Lock()
state = {
    "anomaly_enabled": ANOMALY_ENABLED,
    "anomaly_vehicle": INITIAL_ANOMALY_VEHICLE,
    "anomaly_start": START_TIME + ANOMALY_START_SECONDS,
    "error_rate": DEFAULT_ERROR_RATE,
    "exporter_down_until": 0.0,
}


def reset_state():
    with state_lock:
        state["anomaly_enabled"] = ANOMALY_ENABLED
        state["anomaly_vehicle"] = INITIAL_ANOMALY_VEHICLE
        state["anomaly_start"] = START_TIME + ANOMALY_START_SECONDS
        state["error_rate"] = DEFAULT_ERROR_RATE
        state["exporter_down_until"] = 0.0


def anomaly_rise(now):
    """Temperaturanstieg der Kühlstörung zum Zeitpunkt now (in °C)."""
    with state_lock:
        if not state["anomaly_enabled"] or now < state["anomaly_start"]:
            return 0.0
        minutes = (now - state["anomaly_start"]) / 60.0
    return min(minutes * ANOMALY_RISE_PER_MINUTE, ANOMALY_MAX_RISE)


def simulate_temperature():
    """Simuliert Temperaturdaten; ein Fahrzeug hat eine Kühlstörung."""
    tick = 0

    while True:
        tick += 1
        now = time.time()
        rise = anomaly_rise(now)
        with state_lock:
            anomaly_vehicle = state["anomaly_vehicle"]

        for vehicle in vehicles:
            # Basis-Temperatur mit leichtem Rauschen und Tageszeit-Schwankung
            time_factor = math.sin(tick * 0.01) * 0.3  # Langsame Schwankung
            noise = random.gauss(0, 0.2)

            temp_a = vehicle["base_temp"] + vehicle["sensor_a_offset"] + time_factor + noise
            temp_b = vehicle["base_temp"] + vehicle["sensor_b_offset"] + time_factor + noise + random.gauss(0, 0.1)

            # Anomalie: Ein Fahrzeug hat Kühlungsproblem (Temperatur steigt langsam)
            if vehicle["id"] == anomaly_vehicle and rise > 0:
                temp_a += rise
                temp_b += rise

                # Abweichung zählen wenn über Schwellwert
                if temp_a > 8.0:
                    severity = "critical" if temp_a > 10.0 else "warning"
                    deviation_total.labels(
                        vehicle_id=vehicle["id"],
                        severity=severity,
                        zone=vehicle["zone"],
                    ).inc()

            # Metriken setzen
            temperature_current.labels(
                vehicle_id=vehicle["id"],
                sensor_id="sensor-a",
                zone=vehicle["zone"],
                route=vehicle["route"],
            ).set(round(temp_a, 2))

            temperature_current.labels(
                vehicle_id=vehicle["id"],
                sensor_id="sensor-b",
                zone=vehicle["zone"],
                route=vehicle["route"],
            ).set(round(temp_b, 2))

        time.sleep(10)  # Alle 10 Sekunden neue Werte (≈ alle 30s in Realität)


def simulate_requests():
    """Simuliert HTTP-Requests mit realistischen Mustern."""
    while True:
        with state_lock:
            error_rate = state["error_rate"]

        for service in SERVICES:
            # Basis-Request-Rate
            if service == "order-service":
                request_count = random.randint(5, 20)
                endpoints = ["/api/v1/orders", "/api/v1/orders/{id}", "/api/v1/orders/{id}/status"]
            elif service == "cold-chain-service":
                request_count = random.randint(10, 50)
                endpoints = ["/api/v1/measurements", "/api/v1/alerts", "/api/v1/vehicles/{id}/status"]
            elif service == "notification-service":
                request_count = random.randint(2, 10)
                endpoints = ["/api/v1/notifications", "/api/v1/notifications/send"]
            else:
                request_count = random.randint(3, 15)
                endpoints = ["/api/v1/warehouse/stock", "/api/v1/warehouse/reserve"]

            for _ in range(request_count):
                endpoint = random.choice(endpoints)
                method = random.choice(["GET", "GET", "GET", "POST", "PUT"])

                # Latenz simulieren (normalerweise schnell, manchmal langsam)
                if service == "warehouse-adapter" and random.random() < 0.1:
                    # Legacy-ERP ist manchmal langsam
                    latency = random.uniform(1.0, 4.0)
                elif random.random() < 0.02:
                    # Gelegentliche Spikes
                    latency = random.uniform(2.0, 8.0)
                else:
                    latency = random.uniform(0.01, 0.3)

                # Status Code (meistens 200, manchmal Fehler)
                if random.random() < error_rate:
                    status = random.choice(["500", "502", "503"])
                elif random.random() < 0.05:
                    status = random.choice(["400", "404", "429"])
                else:
                    status = "200"

                http_requests_total.labels(
                    service=service,
                    method=method,
                    endpoint=endpoint,
                    status_code=status,
                ).inc()

                http_request_duration_seconds.labels(
                    service=service,
                    method=method,
                    endpoint=endpoint,
                ).observe(latency)

        # MQTT-Nachrichten simulieren
        for vehicle in vehicles:
            mqtt_messages_received_total.labels(
                topic=f"sensors/{vehicle['id']}/temperature",
                qos="1",
            ).inc()

            status = "success" if random.random() > 0.01 else "error"
            mqtt_messages_processed_total.labels(
                topic=f"sensors/{vehicle['id']}/temperature",
                status=status,
            ).inc()

            processing_time = random.uniform(0.001, 0.05)
            event_processing_duration_seconds.labels(
                event_type="temperature_measurement",
            ).observe(processing_time)

        # Bestellungen simulieren
        if random.random() < 0.3:
            customer_type = random.choice(["hospital", "pharmacy", "wholesaler"])
            priority = random.choice(["normal", "normal", "normal", "express"])
            orders_created_total.labels(
                customer_type=customer_type,
                priority=priority,
            ).inc()

        time.sleep(5)


def simulate_system_metrics():
    """Simuliert System-Metriken (CPU, Memory)."""
    while True:
        for service in SERVICES:
            for pod_idx in range(random.randint(2, 3)):
                pod_name = f"{service}-{pod_idx}"

                # CPU: 10-60% normal, gelegentlich höher
                cpu = random.uniform(10, 60)
                if service == "cold-chain-service":
                    cpu += 15  # Cold-Chain braucht mehr CPU
                service_cpu_usage.labels(service=service, pod=pod_name).set(round(cpu, 1))

                # Memory: 256MB - 1GB
                base_memory = {"order-service": 512, "cold-chain-service": 768,
                               "notification-service": 256, "warehouse-adapter": 384}
                memory = base_memory.get(service, 512) * 1024 * 1024
                memory += random.uniform(-50, 100) * 1024 * 1024
                service_memory_usage_bytes.labels(service=service, pod=pod_name).set(int(memory))

        # Service-Verfügbarkeit (meistens up)
        for service in SERVICES:
            is_up = 1 if random.random() > 0.005 else 0  # 99.5% Verfügbarkeit
            service_up.labels(service=service).set(is_up)

        # Aktive Transporte
        for zone in ZONES:
            count = random.randint(8, 20)
            active_transports.labels(zone=zone).set(count)

        time.sleep(15)


# ============================================================
# HTTP-Server: Metriken (Port 8000)
# ============================================================
class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != "/metrics":
            self._send(404, "text/plain; charset=utf-8", b"Metriken unter /metrics\n")
            return
        with state_lock:
            down = time.time() < state["exporter_down_until"]
        if down:
            # Simulierter Ausfall: Prometheus sieht den Scrape als fehlgeschlagen (up == 0)
            self._send(503, "text/plain; charset=utf-8", b"Exporter simuliert Ausfall\n")
            return
        self._send(200, CONTENT_TYPE_LATEST, generate_latest())

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Scrapes nicht protokollieren


# ============================================================
# HTTP-Server: Steuerseite für Störungen (Port 8001)
# ============================================================
CONTROL_PAGE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><title>MedFlow Exporter – Steuerung</title>
<style>body{{font-family:sans-serif;margin:2em;max-width:50em}}td,th{{padding:.3em .8em;text-align:left}}
.msg{{background:#e8f4e8;padding:.6em;border-left:4px solid #3a3}}</style></head><body>
<h1>MedFlow Cold-Chain Exporter – Steuerung</h1>
{message}
<h2>Aktueller Zustand</h2>
<table>
<tr><th>Anomalie aktiv</th><td>{anomaly_enabled}</td></tr>
<tr><th>Anomalie-Fahrzeug</th><td>{anomaly_vehicle}</td></tr>
<tr><th>Anomalie-Status</th><td>{anomaly_status}</td></tr>
<tr><th>Aktueller Anstieg</th><td>+{rise:.1f} °C</td></tr>
<tr><th>Fehlerquote (5xx)</th><td>{error_rate:.0%}</td></tr>
<tr><th>Exporter-Ausfall</th><td>{down_status}</td></tr>
</table>
<h2>Störungen auslösen</h2>
<ul>
<li><a href="/chaos/error-rate?value=0.10">Fehlerquote auf 10 % setzen</a> (HighErrorRate feuert nach ca. 5–10 Min.)</li>
<li><a href="/chaos/anomaly?vehicle={next_vehicle}">Neue Kühlstörung bei {next_vehicle} sofort starten</a></li>
<li><a href="/chaos/exporter-down?seconds=120">Exporter 2 Minuten ausfallen lassen</a> (ColdChainServiceDown)</li>
<li><a href="/chaos/reset">Alles zurücksetzen</a></li>
</ul>
<p>Parameter anpassbar, z. B. <code>/chaos/error-rate?value=0.25</code>,
<code>/chaos/anomaly?vehicle=VH-007</code>, <code>/chaos/exporter-down?seconds=300</code>.</p>
</body></html>
"""


class ControlHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        message = ""

        try:
            if url.path == "/chaos/error-rate":
                value = float(params.get("value", ["0.10"])[0])
                if not 0.0 <= value <= 1.0:
                    raise ValueError("value muss zwischen 0 und 1 liegen")
                with state_lock:
                    state["error_rate"] = value
                message = f"Fehlerquote auf {value:.0%} gesetzt."
            elif url.path == "/chaos/anomaly":
                vehicle = params.get("vehicle", [""])[0]
                if vehicle not in VEHICLE_IDS:
                    raise ValueError(f"Unbekanntes Fahrzeug: {vehicle!r} (gültig: VH-001 bis {VEHICLE_IDS[-1]})")
                with state_lock:
                    state["anomaly_enabled"] = True
                    state["anomaly_vehicle"] = vehicle
                    state["anomaly_start"] = time.time()
                message = f"Kühlstörung bei {vehicle} gestartet."
            elif url.path == "/chaos/exporter-down":
                seconds = int(params.get("seconds", ["120"])[0])
                with state_lock:
                    state["exporter_down_until"] = time.time() + seconds
                message = f"Exporter fällt für {seconds} Sekunden aus."
            elif url.path == "/chaos/reset":
                reset_state()
                message = "Normalzustand wiederhergestellt (Anomalie gemäss Startkonfiguration)."
            elif url.path != "/":
                self._send(404, "Unbekannter Pfad. Steuerseite: /")
                return
        except ValueError as exc:
            self._send(400, f"Ungültiger Parameter: {exc}")
            return

        if message:
            print(f"[Steuerung] {message}", flush=True)
            # Nach einer Aktion zur Übersicht zurück, damit ein Reload die Aktion nicht wiederholt
            self.send_response(303)
            self.send_header("Location", "/?msg=" + quote_plus(message))
            self.end_headers()
            return

        now = time.time()
        rise = anomaly_rise(now)
        with state_lock:
            s = dict(state)
        if not s["anomaly_enabled"]:
            anomaly_status = "aus"
        elif now < s["anomaly_start"]:
            anomaly_status = f"startet in {int(s['anomaly_start'] - now)} s"
        else:
            anomaly_status = f"läuft seit {int(now - s['anomaly_start'])} s"
        down_left = s["exporter_down_until"] - now
        msg = params.get("msg", [""])[0]
        page = CONTROL_PAGE.format(
            message=f'<p class="msg">{html.escape(msg)}</p>' if msg else "",
            anomaly_enabled="ja" if s["anomaly_enabled"] else "nein",
            anomaly_vehicle=s["anomaly_vehicle"],
            anomaly_status=anomaly_status,
            rise=rise,
            error_rate=s["error_rate"],
            down_status=f"noch {int(down_left)} s" if down_left > 0 else "nein",
            next_vehicle=random.choice([v for v in VEHICLE_IDS if v != s["anomaly_vehicle"]]),
        )
        self._send(200, page, content_type="text/html; charset=utf-8")

    def _send(self, code, text, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


# ============================================================
# Start
# ============================================================
if __name__ == "__main__":
    print(f"🚀 MedFlow Cold-Chain Exporter gestartet auf Port {PORT}", flush=True)
    print(f"   Fahrzeuge: {VEHICLE_COUNT}", flush=True)
    print(f"   Anomalie aktiv: {ANOMALY_ENABLED} (Fahrzeug {state['anomaly_vehicle']}, "
          f"Start nach {int(ANOMALY_START_SECONDS)} s)", flush=True)
    print(f"   Metriken:  http://localhost:{PORT}/metrics", flush=True)
    print(f"   Steuerung: http://localhost:{CONTROL_PORT}/", flush=True)

    # Simulations-Threads starten
    threads = [
        threading.Thread(target=simulate_temperature, daemon=True),
        threading.Thread(target=simulate_requests, daemon=True),
        threading.Thread(target=simulate_system_metrics, daemon=True),
        threading.Thread(
            target=ThreadingHTTPServer(("", CONTROL_PORT), ControlHandler).serve_forever,
            daemon=True,
        ),
    ]

    for t in threads:
        t.start()

    # Main-Thread bedient die Metriken für Prometheus
    ThreadingHTTPServer(("", PORT), MetricsHandler).serve_forever()
