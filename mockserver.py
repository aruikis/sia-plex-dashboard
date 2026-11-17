"""Fixture HTTP server for --mock mode and tests.

Serves the JSON files in fixtures/ in response to the same URL paths the
real clients request, so the whole stack (HTTP client -> parsers -> poller
-> web API) runs end-to-end with no real hostd, Plex or Siascan involved.
Routing is by path suffix, so the hostd /api prefix (or its absence) and
the various base URLs all land on the same fixtures.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ordered: first match wins. (path suffix, fixture filename)
ROUTES = [
    ("/metrics/host", "siascan_host_metrics.json"),
    ("/metrics/block", "siascan_block_metrics.json"),
    ("/exchange-rate/usd", "siascan_exchange_rate.json"),
    ("/consensus/tip", "hostd_consensus_tip.json"),
    ("/index/tip", "hostd_index_tip.json"),
    ("/syncer/peers", "hostd_peers.json"),
    ("/status/sessions", "plex_sessions.json"),
    ("/library/sections", "plex_sections.json"),          # exact, handled below
    ("/state", "hostd_state.json"),
    ("/metrics", "hostd_metrics.json"),
    ("/volumes", "hostd_volumes.json"),
    ("/wallet", "hostd_wallet.json"),
    ("/alerts", "hostd_alerts.json"),
    ("/settings", "hostd_settings.json"),
]


def fixture_for(path):
    """Map a request path to a fixture filename (or None)."""
    path = urlparse(path).path.rstrip("/") or "/"
    if path == "/":
        return "plex_root.json"
    if "/library/sections/" in path and path.endswith("/all"):
        return "plex_section_all.json"
    if path.endswith("/library/sections"):
        return "plex_sections.json"
    for suffix, filename in ROUTES:
        if path.endswith(suffix):
            return filename
    return None


def make_handler(fixtures_dir):
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            filename = fixture_for(self.path)
            if filename is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error": "no fixture"}')
                return
            full = os.path.join(fixtures_dir, filename)
            try:
                with open(full, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error": "fixture missing"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return FixtureHandler


def serve_fixtures(fixtures_dir, port=0):
    """Start the fixture server on an ephemeral port.

    Returns (server, port). Caller owns shutdown.
    """
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 make_handler(fixtures_dir))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def load_fixture(fixtures_dir, filename):
    with open(os.path.join(fixtures_dir, filename), "r",
              encoding="utf-8") as fh:
        return json.load(fh)
