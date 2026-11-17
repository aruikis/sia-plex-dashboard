"""End-to-end smoke test.

Starts the real app as a subprocess in --mock mode (fixture HTTP server
standing in for hostd, Plex and Siascan), then verifies over HTTP that the
page and both /api endpoints respond 200 and carry the fixture values.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.tmp = tempfile.mkdtemp(prefix="dashboard-smoke-")
        cmd = [sys.executable, os.path.join(APP_DIR, "app.py"),
               "--mock", "--port", str(cls.port),
               "--db", os.path.join(cls.tmp, "test.db"),
               "--config", os.path.join(cls.tmp, "no-such-config.json")]
        cls.proc = subprocess.Popen(
            cmd, cwd=APP_DIR, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("app exited early:\n" +
                                   cls.proc.stdout.read())
            try:
                status, _ = get(cls.base + "/api/sia", timeout=2)
                if status == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("app did not come up in 30s")
        # wait until the mock polls have landed (hostd view online)
        deadline = time.time() + 30
        while time.time() < deadline:
            _, body = get(cls.base + "/api/sia")
            if json.loads(body).get("online"):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("mock hostd poll never landed")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def test_page_serves_with_both_tabs(self):
        status, body = get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("Sia Node", body)
        self.assertIn("Plex", body)
        self.assertIn('id="tab-sia"', body)
        self.assertIn('id="tab-plex"', body)

    def test_api_sia_fixture_values(self):
        status, body = get(self.base + "/api/sia")
        self.assertEqual(status, 200)
        sia = json.loads(body)
        self.assertTrue(sia["online"])
        self.assertEqual(sia["state"]["version"], "v2.12.1")
        self.assertEqual(sia["wallet"]["confirmed_sc"], 125.0)
        self.assertEqual(sia["sync"]["status"], "synced")
        self.assertEqual(sia["sync"]["network_height"], 602209)
        self.assertEqual(sia["peers"]["peer_count"], 12)
        volumes = sia["volumes"]
        self.assertEqual(volumes["unavailable_count"], 1)
        dead = [v for v in volumes["volumes"] if not v["available"]]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["path"], "/Volumes/Seagate 20/Sia/sia")
        self.assertEqual(sia["alerts"]["total"], 2)
        self.assertAlmostEqual(
            sia["pricing"]["storage_sc_per_tb_month"], 1831.68, places=2)
        network = sia["network"]
        self.assertEqual(network["active_hosts"], 513)
        self.assertEqual(network["used_storage_bytes"], 2760000000000000)
        self.assertEqual(network["sc_usd"], 0.000058)

    def test_api_plex_fixture_values(self):
        status, body = get(self.base + "/api/plex")
        self.assertEqual(status, 200)
        plex = json.loads(body)
        self.assertTrue(plex["online"])
        self.assertEqual(plex["server"]["name"], "RuikisFlix")
        self.assertEqual(plex["session_count"], 2)
        self.assertEqual(plex["bandwidth_mbps"], 32.5)
        titles = [s["title"] for s in plex["sessions"]]
        self.assertIn("Dune: Part Two", titles)
        modes = {s["title"]: s["mode"] for s in plex["sessions"]}
        self.assertEqual(modes["Dune: Part Two"], "Direct Play")
        self.assertEqual(len(plex["sections"]), 3)
        self.assertEqual(plex["sections"][0]["count"], 1234)

    def test_api_history(self):
        status, body = get(
            self.base + "/api/history?metric=revenue_potential_sc&hours=24")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["metric"], "revenue_potential_sc")
        self.assertGreaterEqual(len(payload["points"]), 10)

    def test_api_history_rejects_unknown_metric(self):
        try:
            get(self.base + "/api/history?metric=bogus")
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_unknown_path_404(self):
        try:
            get(self.base + "/nope")
            self.fail("expected HTTP 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


if __name__ == "__main__":
    unittest.main()
