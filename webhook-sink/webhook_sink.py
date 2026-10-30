"""
MedFlow AG – Webhook-Empfänger (Simulation)
===========================================
Ersetzt PagerDuty, Slack und E-Mail im Lab. Nimmt Alertmanager-Webhooks
auf /pagerduty, /slack, /email und /default entgegen und zeigt:
- eine Zeile pro Alert im Log (docker compose logs -f webhook-sink)
- die letzten 50 Meldungen unter http://localhost:9999/
"""

import html
import json
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9999
CHANNELS = {
    "/pagerduty": "PagerDuty (critical)",
    "/slack": "Slack (warning)",
    "/email": "E-Mail (info)",
    "/default": "Default",
}

messages = deque(maxlen=50)

PAGE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><meta http-equiv="refresh" content="10">
<title>MedFlow Webhook-Empfänger</title>
<style>body{{font-family:sans-serif;margin:2em}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:.3em .6em;text-align:left}}
.firing{{color:#b00}}.resolved{{color:#080}}</style></head><body>
<h1>MedFlow Webhook-Empfänger</h1>
<p>Letzte 50 Meldungen, neueste zuoberst. Seite lädt alle 10 Sekunden neu.</p>
<table><tr><th>Zeit</th><th>Kanal (Pfad)</th><th>Receiver</th><th>Status</th>
<th>Alert</th><th>Severity</th><th>Betroffen</th><th>Summary</th></tr>
{rows}
</table></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, "Ungültiges JSON\n")
            return

        channel = CHANNELS.get(self.path, self.path)
        receiver = payload.get("receiver", "?")
        now = datetime.now().strftime("%H:%M:%S")

        for alert in payload.get("alerts", []):
            labels = alert.get("labels", {})
            entry = {
                "time": now,
                "channel": f"{channel} ({self.path})",
                "receiver": receiver,
                "status": alert.get("status", "?"),
                "alertname": labels.get("alertname", "?"),
                "severity": labels.get("severity", "-"),
                "target": labels.get("vehicle_id") or labels.get("service") or labels.get("instance", "-"),
                "summary": alert.get("annotations", {}).get("summary", ""),
            }
            messages.appendleft(entry)
            print(
                f"{now} {self.path:<11} receiver={receiver:<20} {entry['status']:<8} "
                f"{entry['alertname']} severity={entry['severity']} betroffen={entry['target']}",
                flush=True,
            )

        self._send(200, "OK\n")

    def do_GET(self):
        rows = "\n".join(
            "<tr>" + "".join(
                f'<td class="{html.escape(m["status"])}">{html.escape(m[k])}</td>' if k == "status"
                else f"<td>{html.escape(m[k])}</td>"
                for k in ("time", "channel", "receiver", "status", "alertname", "severity", "target", "summary")
            ) + "</tr>"
            for m in messages
        ) or '<tr><td colspan="8">Noch keine Meldungen empfangen.</td></tr>'
        self._send(200, PAGE.format(rows=rows), content_type="text/html; charset=utf-8")

    def _send(self, code, text, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"📨 Webhook-Empfänger läuft: http://localhost:{PORT}/", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
