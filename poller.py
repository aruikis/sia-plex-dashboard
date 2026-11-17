"""Polling engine for the dashboard.

Three sources, three loops, one process:

  hostd    every `hostd_interval` s   (default 60)
  plex     every `plex_interval` s    (default 30)
  siascan  every `siascan_interval` s (default 900 = 15 min)

STRICTLY READ-ONLY: the clients below only ever issue HTTP GET requests.
Nothing in this file can change state on hostd, Plex or Siascan.

Failure model: a failed poll never raises out of its loop. The source is
marked offline (with the error and the time of the last successful poll) and
the last good data is kept and flagged stale, so the page can say "node
offline — showing last known values" instead of going blank. This matters
because hostd is started by hand on the M1 and may simply not be running
when the dashboard starts.
"""

import base64
import json
import threading
import time
import urllib.error
import urllib.request

import parsers


class HttpError(Exception):
    pass


def http_get_json(url, headers=None, timeout=10):
    """One GET, JSON-decoded. Raises HttpError on any failure."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise HttpError("HTTP %s from %s" % (exc.code, url))
    except Exception as exc:  # DNS, refused, timeout, TLS...
        raise HttpError("%s: %s" % (url, exc))
    try:
        return json.loads(body)
    except ValueError:
        raise HttpError("non-JSON response from %s" % url)


class HostdClient:
    """hostd v2 API. Basic auth: empty username, API password as password
    (the scheme the official Go client uses)."""

    def __init__(self, base_url, api_prefix, password):
        self.base = base_url.rstrip("/")
        self.prefix = (api_prefix or "").rstrip("/")
        token = base64.b64encode((":" + (password or "")).encode()).decode()
        self.headers = {"Authorization": "Basic " + token}

    def get(self, path):
        return http_get_json(self.base + self.prefix + path,
                             headers=self.headers)


class PlexClient:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        self.headers = {"X-Plex-Token": token or "",
                        "Accept": "application/json"}

    def get(self, path):
        return http_get_json(self.base + path, headers=self.headers)


class SiascanClient:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def get(self, path):
        return http_get_json(self.base + path)


class Poller:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.hostd = HostdClient(cfg["hostd_url"], cfg.get("hostd_api_prefix"),
                                 cfg.get("hostd_api_password"))
        self.plex = PlexClient(cfg["plex_url"], cfg.get("plex_token"))
        self.siascan = SiascanClient(cfg["siascan_url"])
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sia = {"online": False, "stale": True, "polled_at": None,
                     "last_ok_at": None, "error": "not polled yet"}
        self._network = {"polled_at": None, "error": "not polled yet"}
        self._plex = {"online": False, "stale": True, "polled_at": None,
                      "last_ok_at": None, "error": "not polled yet"}

    # ------------------------------------------------------------------
    # hostd
    # ------------------------------------------------------------------
    def poll_hostd(self):
        now = time.time()
        try:
            state = parsers.parse_state(self.hostd.get("/state"))
            metrics = parsers.parse_metrics(self.hostd.get("/metrics"))
            volumes = parsers.parse_volumes(self.hostd.get("/volumes"))
            wallet = parsers.parse_wallet(self.hostd.get("/wallet"))
            alerts = parsers.parse_alerts(self.hostd.get("/alerts"))
            consensus_h = parsers.parse_tip(self.hostd.get("/consensus/tip"))
            index_h = parsers.parse_tip(self.hostd.get("/index/tip"))
            peers = parsers.parse_peers(self.hostd.get("/syncer/peers"))
            settings = parsers.parse_settings(self.hostd.get("/settings"))
        except HttpError as exc:
            with self._lock:
                self._sia.update({"online": False, "stale": True,
                                  "polled_at": now, "error": str(exc)})
            return False

        uptime_s = None
        if state.get("start_time"):
            try:
                from datetime import datetime, timezone
                started = datetime.fromisoformat(
                    state["start_time"].replace("Z", "+00:00"))
                uptime_s = max(0, int(
                    (datetime.now(timezone.utc) - started).total_seconds()))
            except (ValueError, TypeError):
                uptime_s = None

        with self._lock:
            network_height = self._network.get("network_height")
        sync = parsers.compute_sync(consensus_h, index_h, network_height)

        pricing = settings.get("pricing") or metrics.get("pricing") or {}
        view = {
            "online": True, "stale": False, "polled_at": now,
            "last_ok_at": now, "error": None,
            "state": state, "uptime_seconds": uptime_s,
            "sync": sync, "peers": peers,
            "revenue": metrics.get("revenue"),
            "contracts": metrics.get("contracts"),
            "storage_metrics": metrics.get("storage"),
            "data": metrics.get("data"),
            "volumes": volumes,
            "wallet": wallet,
            "alerts": alerts,
            "settings": settings,
            "pricing": pricing,
        }
        # fiat values (need the Siascan rate; may be None on first polls)
        with self._lock:
            rate = self._network.get("sc_usd")
            self._sia = view
        if rate:
            confirmed = wallet.get("confirmed_sc")
            potential_total = (view["revenue"] or {}).get(
                "potential", {}).get("total")
            view["fiat"] = {
                "sc_usd": rate,
                "balance_usd": (confirmed * rate
                                if confirmed is not None else None),
                "potential_usd": (potential_total * rate
                                  if potential_total is not None else None),
            }
        self.store.add_snapshot("hostd", view, ts=now)
        rev = view["revenue"] or {}
        self.store.add_history("revenue_potential_sc",
                               (rev.get("potential") or {}).get("total"), now)
        self.store.add_history("revenue_earned_sc",
                               (rev.get("earned") or {}).get("total"), now)
        self.store.add_history(
            "storage_used_bytes",
            (metrics.get("storage") or {}).get("physical_bytes"), now)
        self.store.add_history("wallet_balance_sc",
                               wallet.get("confirmed_sc"), now)
        self.store.prune(now)
        return True

    # ------------------------------------------------------------------
    # siascan
    # ------------------------------------------------------------------
    def poll_siascan(self):
        now = time.time()
        try:
            host = parsers.parse_siascan_host(
                self.siascan.get("/metrics/host"))
            block = parsers.parse_siascan_block(
                self.siascan.get("/metrics/block"))
            rate = parsers.parse_exchange_rate(
                self.siascan.get("/exchange-rate/usd"))
        except HttpError as exc:
            with self._lock:
                self._network.update({"polled_at": now, "error": str(exc)})
            return False
        network = dict(host)
        network.update(block)
        network.update({"sc_usd": rate, "polled_at": now, "error": None})
        with self._lock:
            self._network = network
            # refresh the sync light now that we have a network height
            sia = self._sia
            if sia.get("sync"):
                sia["sync"] = parsers.compute_sync(
                    sia["sync"].get("consensus_height"),
                    sia["sync"].get("index_height"),
                    network.get("network_height"))
        self.store.add_snapshot("siascan", network, ts=now)
        return True

    # ------------------------------------------------------------------
    # plex
    # ------------------------------------------------------------------
    def poll_plex(self):
        now = time.time()
        try:
            identity = parsers.parse_plex_identity(self.plex.get("/"))
            sessions = parsers.parse_plex_sessions(
                self.plex.get("/status/sessions"))
            sections = parsers.parse_plex_sections(
                self.plex.get("/library/sections"))
        except HttpError as exc:
            with self._lock:
                self._plex.update({"online": False, "stale": True,
                                   "polled_at": now, "error": str(exc)})
            return False
        # per-section item counts: one cheap size-0 query each; a section
        # that fails just keeps count=None. Read-only.
        for section in sections:
            if section.get("key") is None:
                continue
            try:
                count = parsers.parse_plex_section_count(self.plex.get(
                    "/library/sections/%s/all?X-Plex-Container-Size=0"
                    "&X-Plex-Container-Start=0" % section["key"]))
                section["count"] = count
            except HttpError:
                pass
        view = {
            "online": True, "stale": False, "polled_at": now,
            "last_ok_at": now, "error": None,
            "server": identity,
            "sessions": sessions.get("sessions", []),
            "session_count": sessions.get("count", 0),
            "bandwidth_mbps": sessions.get("bandwidth_mbps", 0),
            "sections": sections,
        }
        with self._lock:
            self._plex = view
        self.store.add_snapshot("plex", view, ts=now)
        self.store.add_history("plex_sessions", view["session_count"], now)
        self.store.add_history("plex_bandwidth_mbps",
                               view["bandwidth_mbps"], now)
        return True

    # ------------------------------------------------------------------
    # views for the web layer
    # ------------------------------------------------------------------
    def sia_view(self):
        with self._lock:
            view = dict(self._sia)
            view["network"] = dict(self._network)
            return view

    def plex_view(self):
        with self._lock:
            return dict(self._plex)

    # ------------------------------------------------------------------
    # loops
    # ------------------------------------------------------------------
    def _loop(self, fn, interval):
        while not self._stop.is_set():
            fn()
            self._stop.wait(interval)

    def start(self):
        threads = [
            threading.Thread(target=self._loop,
                             args=(self.poll_siascan,
                                   self.cfg["siascan_interval"]),
                             daemon=True, name="poll-siascan"),
            threading.Thread(target=self._loop,
                             args=(self.poll_hostd,
                                   self.cfg["hostd_interval"]),
                             daemon=True, name="poll-hostd"),
            threading.Thread(target=self._loop,
                             args=(self.poll_plex,
                                   self.cfg["plex_interval"]),
                             daemon=True, name="poll-plex"),
        ]
        for thread in threads:
            thread.start()
        return threads

    def stop(self):
        self._stop.set()
