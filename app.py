#!/usr/bin/env python3
"""Sia + Plex dashboard — single process, Python 3 standard library only.

Run:  python3 app.py [--config config.json] [--port 8899] [--db PATH]
                     [--mock]

The poller (poller.py) keeps hostd / Siascan / Plex snapshots fresh in the
background; this file serves one dark-themed page with two tabs ("Sia Node"
and "Plex") plus the JSON endpoints the page consumes:

  GET /                              the page
  GET /api/sia                       latest Sia view model (+ network block)
  GET /api/plex                      latest Plex view model
  GET /api/history?metric=X&hours=N  time series for the charts

Bind address is all interfaces on purpose: Anthony's network design keeps
this LAN-only (no port forward for 8899 — see README/INSTALL). Credentials
live only in config.json on the machine running the app; nothing secret is
ever served to the page.
"""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mockserver  # noqa: E402
from poller import Poller  # noqa: E402
from store import Store  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "hostd_url": "http://127.0.0.1:9980",
    "hostd_api_prefix": "/api",
    "hostd_api_password": "",
    "plex_url": "http://192.168.1.114:32400",
    "plex_token": "",
    "siascan_url": "https://api.siascan.com",
    "port": 8899,
    "db_path": os.path.join(APP_DIR, "dashboard.db"),
    "hostd_interval": 60,
    "plex_interval": 30,
    "siascan_interval": 900,
}

HISTORY_METRICS = (
    "revenue_potential_sc", "revenue_earned_sc", "storage_used_bytes",
    "wallet_balance_sc", "plex_sessions", "plex_bandwidth_mbps",
)


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return cfg


def seed_mock_history(store):
    """Backfill ~24h of gently-varying history so --mock charts render.

    Deterministic (tiny LCG) — demo data only, never used in real mode.
    """
    now = time.time()
    state = [123456789]

    def rnd():
        state[0] = (1103515245 * state[0] + 12345) % (2 ** 31)
        return state[0] / float(2 ** 31)

    for i in range(96):  # every 15 min for 24 h
        ts = now - (95 - i) * 900
        drift = i / 95.0
        store.add_history("revenue_potential_sc",
                          182.0 + drift * 6.0 + rnd() * 0.8, ts)
        store.add_history("revenue_earned_sc",
                          438.0 + drift * 3.0 + rnd() * 0.4, ts)
        store.add_history("storage_used_bytes",
                          (4150000 + i * 900) * 4194304, ts)
        store.add_history("wallet_balance_sc", 125.0 + rnd() * 1.5, ts)
        store.add_history("plex_sessions",
                          2 if 60 <= i <= 80 else (1 if i % 7 == 0 else 0), ts)
        store.add_history("plex_bandwidth_mbps",
                          32.5 if 60 <= i <= 80 else 0, ts)


def make_handler(poller, store):
    with open(os.path.join(APP_DIR, "index.html"), "rb") as fh:
        page = fh.read()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode("utf-8"),
                       "application/json")

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/sia":
                self._json(poller.sia_view())
            elif path == "/api/plex":
                self._json(poller.plex_view())
            elif path == "/api/history":
                qs = parse_qs(parsed.query)
                metric = (qs.get("metric") or [""])[0]
                if metric not in HISTORY_METRICS:
                    self._json({"error": "unknown metric",
                                "metrics": list(HISTORY_METRICS)}, code=400)
                    return
                try:
                    hours = float((qs.get("hours") or ["24"])[0])
                except ValueError:
                    hours = 24.0
                since = time.time() - hours * 3600
                self._json({"metric": metric,
                            "points": store.history(metric, since)})
            else:
                self._json({"error": "not found"}, code=404)

        def log_message(self, *args):
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sia + Plex dashboard")
    ap.add_argument("--config", default=os.environ.get(
        "DASHBOARD_CONFIG", os.path.join(APP_DIR, "config.json")))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--mock", action="store_true",
                    help="serve built-in fixtures instead of real services")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.port is not None:
        cfg["port"] = args.port
    if args.db:
        cfg["db_path"] = args.db

    fixture_server = None
    if args.mock:
        fixture_server, fixture_port = mockserver.serve_fixtures(
            os.path.join(APP_DIR, "fixtures"))
        base = "http://127.0.0.1:%d" % fixture_port
        cfg.update({"hostd_url": base, "plex_url": base,
                    "siascan_url": base, "hostd_api_prefix": ""})
        print("mock mode: fixtures at %s" % base)

    store = Store(cfg["db_path"])
    if args.mock:
        seed_mock_history(store)
    poller = Poller(cfg, store)
    poller.start()

    server = ThreadingHTTPServer(("0.0.0.0", cfg["port"]),
                                 make_handler(poller, store))
    print("dashboard on http://0.0.0.0:%d (Ctrl+C to stop)" % cfg["port"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        server.shutdown()
        store.close()
        if fixture_server is not None:
            fixture_server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
