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
# Configure utilization (integer percent matches the real endpoint):
#   curl -X POST http://localhost:8765/admin/set \
#     -d '{"five_hour": 97, "seven_day": 50}'
#
# Float fractions in [0.0, 1.0] are also accepted for backward compat
# with older test fixtures (auto-converted to integer percent on input).
#
# Reset:
#   curl -X POST http://localhost:8765/admin/reset
#
# Format note (2026-04-29, Q12): the real oauth/usage endpoint emits
# integer utilization in [0, 100]. SPEC.md §6.3 originally documented
# floats in [0.0, 1.0]. This mock now mirrors the real endpoint so
# tests exercise the integer→float normalization in lib/quota.py.

import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# Internal STATE stores integer percent (0..100) to mirror the real
# endpoint payload shape. /admin/set accepts both int 0..100 and
# float 0..1 (defensive); GET /api/oauth/usage always emits integer.
STATE = {
    "five_hour": 10,
    "seven_day": 30,
    "five_hour_resets_at": None,  # set on first GET
    "seven_day_resets_at": None,
}


def _coerce_pct(v) -> int:
    """Accept int 0..100 or float 0..1 → integer percent 0..100.

    Defensive: rejects None, bools, and non-numeric. Out-of-range
    values clamp to [0, 100].
    """
    if v is None or isinstance(v, bool):
        raise ValueError("utilization must be numeric")
    if isinstance(v, int):
        pct = v
    else:
        f = float(v)
        pct = int(round(f * 100)) if f <= 1.0 else int(round(f))
    return max(0, min(100, pct))


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

            self._send_json(
                200,
                {
                    "five_hour": {
                        "utilization": STATE["five_hour"],
                        "resets_at": STATE["five_hour_resets_at"],
                    },
                    "seven_day": {
                        "utilization": STATE["seven_day"],
                        "resets_at": STATE["seven_day_resets_at"],
                    },
                },
            )
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
                    STATE["five_hour"] = _coerce_pct(payload["five_hour"])
                if "seven_day" in payload:
                    STATE["seven_day"] = _coerce_pct(payload["seven_day"])
                self._send_json(200, {"status": "ok", "state": STATE})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if self.path == "/admin/reset":
            STATE["five_hour"] = 0
            STATE["seven_day"] = 0
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
