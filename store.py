"""SQLite storage for the dashboard.

Schema (kept deliberately simple):

  snapshots(id, ts, source, payload)
      One row per poll. `source` is 'hostd', 'siascan' or 'plex'; `payload`
      is the *normalised* view-model JSON for that poll (post-parse), so the
      history endpoint and any future debugging read clean values, not raw
      upstream shapes.

  history(id, ts, metric, value)
      Flat time series for chartable numbers, extracted at poll time:
      revenue_potential_sc, revenue_earned_sc, storage_used_bytes,
      wallet_balance_sc, plex_sessions, plex_bandwidth_mbps.

Retention: snapshots older than 30 days and history older than 90 days are
pruned on each hostd poll — plenty for home charts, keeps the file small.
"""

import json
import sqlite3
import threading
import time

SNAPSHOT_RETENTION_S = 30 * 24 * 3600
HISTORY_RETENTION_S = 90 * 24 * 3600


class Store:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS snapshots ("
                "id INTEGER PRIMARY KEY, ts REAL NOT NULL, "
                "source TEXT NOT NULL, payload TEXT NOT NULL)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_source_ts "
                "ON snapshots(source, ts)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS history ("
                "id INTEGER PRIMARY KEY, ts REAL NOT NULL, "
                "metric TEXT NOT NULL, value REAL NOT NULL)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_metric_ts "
                "ON history(metric, ts)")
            self._conn.commit()

    def add_snapshot(self, source, payload, ts=None):
        ts = time.time() if ts is None else ts
        with self._lock:
            self._conn.execute(
                "INSERT INTO snapshots(ts, source, payload) VALUES (?, ?, ?)",
                (ts, source, json.dumps(payload)))
            self._conn.commit()

    def add_history(self, metric, value, ts=None):
        if value is None:
            return
        ts = time.time() if ts is None else ts
        with self._lock:
            self._conn.execute(
                "INSERT INTO history(ts, metric, value) VALUES (?, ?, ?)",
                (ts, metric, float(value)))
            self._conn.commit()

    def history(self, metric, since_ts):
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, value FROM history WHERE metric = ? AND ts >= ? "
                "ORDER BY ts ASC", (metric, since_ts)).fetchall()
        return [{"ts": ts, "value": value} for ts, value in rows]

    def prune(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute("DELETE FROM snapshots WHERE ts < ?",
                               (now - SNAPSHOT_RETENTION_S,))
            self._conn.execute("DELETE FROM history WHERE ts < ?",
                               (now - HISTORY_RETENTION_S,))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
