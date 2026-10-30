# MedFlow Cold-Chain Monitoring Lab

Modul 694 – Session 3 | Hands-on mit Grafana, Prometheus & Alertmanager

Dieses Lab simuliert das Cold-Chain-Monitoring der MedFlow AG: 45 Kühlfahrzeuge in den Zonen Bern, Zürich und Lausanne sowie vier Services (order-service, cold-chain-service, notification-service, warehouse-adapter). Ein Fahrzeug bekommt nach einigen Minuten eine Kühlstörung.

```
 medflow-exporter --(Metriken)--> Prometheus --(Alerts)--> Alertmanager --(Webhooks)--> webhook-sink
   :8000 /metrics                   :9090                    :9093                         :9999
   :8001 Steuerung                    |
                                      +--(Abfragen)--> Grafana :3000
```

## Voraussetzungen

- Docker Desktop (oder Docker Engine mit Compose v2), mindestens 4 GB RAM für Docker
- Browser, Terminal (macOS: Terminal, Windows: PowerShell)
- Freie Ports: 3000, 8000, 8001, 9090, 9093, 9999

## Starten

Im Verzeichnis `medflow-monitoring-lab`:

```bash
docker compose up -d --build
```

Der erste Start lädt rund 1 GB Images und dauert je nach Netz einige Minuten.

Prüfen, ob alles läuft:

```bash
docker compose ps
```

Alle fünf Container müssen den Status `running` haben.

## Zugriff

| Dienst | URL | Login |
|---|---|---|
| Grafana | http://localhost:3000 | admin / medflow2024 |
| Prometheus | http://localhost:9090 | – |
| Prometheus Targets | http://localhost:9090/targets | – |
| Prometheus Alerts | http://localhost:9090/alerts | – |
| Alertmanager | http://localhost:9093 | – |
| Exporter (Metriken) | http://localhost:8000/metrics | – |
| Exporter (Steuerung) | http://localhost:8001 | – |
| Webhook-Empfänger | http://localhost:9999 | – |

## Konfiguration ändern

Die Dateien `prometheus/alert_rules.yml` und `alertmanager/alertmanager.yml` bearbeitest du direkt in diesem Verzeichnis. Die Container sehen Änderungen sofort, laden sie aber erst nach einem Signal.

**1. Prüfen** (immer zuerst):

```bash
docker compose exec prometheus promtool check rules /etc/prometheus/alert_rules.yml
docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
```

**2. Neu laden:**

```bash
docker compose kill -s SIGHUP prometheus
docker compose kill -s SIGHUP alertmanager
```

Die Ausgabe «Container prometheus Killed» ist normal: Es wurde nur das Signal zum Neuladen gesendet, der Container läuft weiter.

Ist die Konfiguration fehlerhaft, arbeiten Prometheus und Alertmanager mit der alten Konfiguration weiter. Die Fehlermeldung siehst du im Log:

```bash
docker compose logs --tail 20 prometheus
docker compose logs --tail 20 alertmanager
```

**Routing testen**, ohne auf einen Alert zu warten:

```bash
docker compose exec alertmanager amtool config routes test --config.file=/etc/alertmanager/alertmanager.yml severity=critical
```

Die Ausgabe nennt den Receiver, an den ein Alert mit diesen Labels geht.

## Benachrichtigungen ansehen

Alertmanager sendet alle Meldungen an den Webhook-Empfänger. Er ersetzt PagerDuty (`/pagerduty`), Slack (`/slack`) und E-Mail (`/email`).

- Im Browser: http://localhost:9999 (lädt alle 10 Sekunden neu)
- Im Terminal: `docker compose logs -f webhook-sink`

## Störungen auslösen

Die Steuerseite des Exporters unter http://localhost:8001 zeigt den Zustand der Simulation (betroffenes Fahrzeug, Fehlerquote) und bietet Links, um Störungen auszulösen und zurückzusetzen. Nutze sie, wenn die Lehrperson dazu auffordert.

## Probleme beheben

| Problem | Lösung |
|---|---|
| `port is already allocated` beim Start | Ein anderes Programm nutzt den Port. In `docker-compose.yml` die linke Zahl ändern, z. B. `"3001:3000"`, und Grafana dann unter http://localhost:3001 öffnen. |
| Target `medflow-coldchain` ist DOWN | `docker compose logs medflow-exporter` ansehen; ggf. `docker compose up -d --build medflow-exporter` |
| Neue Alert-Rules erscheinen nicht | Prüfbefehl ausführen, Fehler beheben, erneut `docker compose kill -s SIGHUP prometheus` |
| Grafana zeigt «No data» | Zeitbereich oben rechts prüfen (z. B. «Last 30 minutes»), Query in Prometheus unter http://localhost:9090/graph testen |
| `docker: unknown command: docker compose` | Ältere Installation: `docker-compose` (mit Bindestrich) statt `docker compose` verwenden |

## Aufräumen

Nach der Übung alle Container und Daten entfernen:

```bash
docker compose down -v
```
