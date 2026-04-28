#!/usr/bin/env python3
# tools/mock-quota-server.py
# Fake oauth/usage endpoint for testing lib/quota.py without burning real quota.
#
# Run:
#   python3 tools/mock-quota-server.py [--port 8765]
#
# Test (separate terminal):
#   curl http://localhost:8765/api/oauth/usage \
#     -H 'Authorization: Bearer fake'
#
# Configure utilization:
#   curl -X POST http://localhost:8765/admin/set \
#     -d '{"five_hour": 0.97, "seven_day": 0.5}'
#
# Reset:
#   curl -X POST http://localhost:8765/admin/reset

import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = {
    "five_hour": 0.10,
    "seven_day": 0.30,
    "five_hour_resets_at": None,  # set on first GET
    "seven_day_resets_at": None,
}


def reset_times():
    now = datetime.now(timezone.utc)
    STATE["five_hour_resets_at"] = (now + timedelta(hours=5)).isoformat()
    STATE["seven_day_resets_at"] = (now + timedelta(days=7)).isoformat()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quieter logging
        print(f"[mock-quota] {self.address_string()} - {fmt % args}")

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/oauth/usage":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                self._send_json(401, {"error": "missing bearer token"})
                return

            beta = self.headers.get("anthropic-beta", "")
            if "oauth-2025-04-20" not in beta:
                self._send_json(400, {"error": "missing beta header"})
                return

            if STATE["five_hour_resets_at"] is None:
                reset_times()

            self._send_json(200, {
                "five_hour": {
                    "utilization": STATE["five_hour"],
                    "resets_at": STATE["five_hour_resets_at"],
                },
                "seven_day": {
                    "utilization": STATE["seven_day"],
                    "resets_at": STATE["seven_day_resets_at"],
                },
            })
            return

        if self.path == "/admin/state":
            self._send_json(200, STATE)
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path == "/admin/set":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length))
                if "five_hour" in payload:
                    STATE["five_hour"] = float(payload["five_hour"])
                if "seven_day" in payload:
                    STATE["seven_day"] = float(payload["seven_day"])
                self._send_json(200, {"status": "ok", "state": STATE})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if self.path == "/admin/reset":
            STATE["five_hour"] = 0.0
            STATE["seven_day"] = 0.0
            reset_times()
            self._send_json(200, {"status": "reset", "state": STATE})
            return

        self._send_json(404, {"error": "not found"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    reset_times()
    print(f"[mock-quota] listening on :{args.port}")
    print("[mock-quota] endpoints:")
    print("  GET  /api/oauth/usage    (Bearer + beta header required)")
    print("  POST /admin/set          (set utilization)")
    print("  POST /admin/reset        (reset to 0)")
    print("  GET  /admin/state        (introspect)")

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock-quota] shutting down")


if __name__ == "__main__":
    main()
