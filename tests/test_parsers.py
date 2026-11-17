"""Unit tests for parsers.py against the recorded-shape fixtures.

Expected values are hand-computed from the fixture files:
  - 1 SC = 10^24 hastings; 1 sector = 4 MiB = 4,194,304 bytes
  - storage price per TB/month = per-TB-per-block price x 4320 blocks
  - fixture volumes: used sectors sum 4,150,000; total 20,303,616
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parsers  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class TestDerivedMath(unittest.TestCase):
    def test_sectors_to_bytes(self):
        self.assertEqual(parsers.sectors_to_bytes(1), 4194304)
        self.assertEqual(parsers.sectors_to_bytes(262144), 1099511627776)
        self.assertEqual(parsers.sectors_to_bytes(0), 0)
        self.assertIsNone(parsers.sectors_to_bytes(None))

    def test_currency_conversion(self):
        self.assertEqual(parsers._currency_sc("1000000000000000000000000"), 1.0)
        self.assertEqual(parsers._currency_sc(10 ** 24), 1.0)
        self.assertAlmostEqual(
            parsers._currency_sc("2500000000000000000000000"), 2.5)
        self.assertIsNone(parsers._currency_sc("not-a-number"))
        self.assertIsNone(parsers._currency_sc(None))

    def test_storage_price_per_month(self):
        # 0.424 SC per TB per block * 4320 = 1831.68 SC per TB per month
        self.assertAlmostEqual(
            parsers.storage_price_sc_per_tb_month("424000000000000000000000"),
            1831.68, places=2)
        self.assertIsNone(parsers.storage_price_sc_per_tb_month(None))

    def test_price_per_tb(self):
        self.assertEqual(
            parsers.price_sc_per_tb("30000000000000000000000000"), 30.0)


class TestSyncLogic(unittest.TestCase):
    def test_synced(self):
        sync = parsers.compute_sync(602208, 602207, 602209)
        self.assertEqual(sync["status"], "synced")
        self.assertEqual(sync["delta_consensus"], -1)
        self.assertEqual(sync["delta_index"], -2)

    def test_catching_up(self):
        self.assertEqual(
            parsers.compute_sync(602200, 602200, 602209)["status"],
            "catching_up")

    def test_stalled(self):
        self.assertEqual(
            parsers.compute_sync(602100, 602100, 602209)["status"], "stalled")

    def test_unknown_when_missing(self):
        self.assertEqual(
            parsers.compute_sync(None, 602207, 602209)["status"], "unknown")

    def test_no_network_height_uses_tip_agreement(self):
        self.assertEqual(parsers.compute_sync(100, 100, None)["status"],
                         "synced")
        self.assertEqual(parsers.compute_sync(100, 90, None)["status"],
                         "catching_up")


class TestHostdParsers(unittest.TestCase):
    def test_state(self):
        state = parsers.parse_state(load("hostd_state.json"))
        self.assertEqual(state["version"], "v2.12.1")
        self.assertEqual(state["last_announcement_height"], 602150)

    def test_metrics_revenue_split(self):
        metrics = parsers.parse_metrics(load("hostd_metrics.json"))
        pot = metrics["revenue"]["potential"]
        earn = metrics["revenue"]["earned"]
        self.assertEqual(pot["storage"], 150.0)
        self.assertEqual(pot["ingress"], 6.0)
        self.assertEqual(pot["egress"], 24.0)
        self.assertEqual(pot["rpc"], 5.0)
        self.assertEqual(pot["total"], 185.0)
        self.assertEqual(earn["total"], 440.0)

    def test_metrics_contracts_and_storage(self):
        metrics = parsers.parse_metrics(load("hostd_metrics.json"))
        contracts = metrics["contracts"]
        self.assertEqual(contracts["active"], 42)
        self.assertEqual(contracts["rejected"], 17)
        self.assertEqual(contracts["failed"], 3)
        self.assertEqual(contracts["locked_collateral_sc"], 900.0)
        self.assertEqual(contracts["risked_collateral_sc"], 120.0)
        storage = metrics["storage"]
        self.assertEqual(storage["total_bytes"],
                         parsers.sectors_to_bytes(20303616))
        self.assertEqual(storage["physical_bytes"],
                         parsers.sectors_to_bytes(4150000))
        self.assertEqual(storage["lost_sectors"], 0)
        self.assertEqual(metrics["wallet_balance_sc"], 125.0)

    def test_volumes_with_unavailable(self):
        result = parsers.parse_volumes(load("hostd_volumes.json"))
        volumes = result["volumes"]
        self.assertEqual(len(volumes), 4)
        self.assertEqual(result["unavailable_count"], 1)
        self.assertEqual(result["used_bytes"],
                         parsers.sectors_to_bytes(4150000))
        self.assertEqual(result["total_bytes"],
                         parsers.sectors_to_bytes(20303616))
        by_id = {v["id"]: v for v in volumes}
        dead = by_id[6]
        self.assertFalse(dead["available"])
        self.assertEqual(dead["status"], "unavailable")
        self.assertEqual(dead["path"], "/Volumes/Seagate 20/Sia/sia")
        self.assertTrue(dead["errors"])
        good = by_id[2]
        self.assertTrue(good["available"])
        self.assertEqual(good["used_bytes"], parsers.sectors_to_bytes(900000))
        self.assertAlmostEqual(good["pct_full"],
                               100.0 * 900000 / 3814697, places=3)
        self.assertEqual(good["failed_reads"], 2)
        self.assertEqual(good["successful_writes"], 18901)

    def test_wallet(self):
        wallet = parsers.parse_wallet(load("hostd_wallet.json"))
        self.assertEqual(wallet["spendable_sc"], 125.0)
        self.assertEqual(wallet["confirmed_sc"], 125.0)
        self.assertEqual(wallet["unconfirmed_sc"], 2.5)
        self.assertEqual(wallet["immature_sc"], 10.0)

    def test_alerts(self):
        alerts = parsers.parse_alerts(load("hostd_alerts.json"))
        self.assertEqual(alerts["total"], 2)
        self.assertEqual(alerts["counts"], {"error": 1, "warning": 1})
        self.assertEqual(alerts["alerts"][0]["severity"], "error")

    def test_tips_and_peers(self):
        self.assertEqual(parsers.parse_tip(load("hostd_consensus_tip.json")),
                         602208)
        self.assertEqual(parsers.parse_tip(load("hostd_index_tip.json")),
                         602207)
        self.assertEqual(
            parsers.parse_peers(load("hostd_peers.json"))["peer_count"], 12)

    def test_settings_pricing(self):
        settings = parsers.parse_settings(load("hostd_settings.json"))
        self.assertTrue(settings["accepting_contracts"])
        pricing = settings["pricing"]
        self.assertAlmostEqual(pricing["storage_sc_per_tb_month"], 1831.68,
                               places=2)
        self.assertEqual(pricing["ingress_sc_per_tb"], 30.0)
        self.assertEqual(pricing["egress_sc_per_tb"], 3431.0)
        self.assertEqual(pricing["contract_sc"], 1.0)


class TestSiascanParsers(unittest.TestCase):
    def test_host_metrics(self):
        host = parsers.parse_siascan_host(load("siascan_host_metrics.json"))
        self.assertEqual(host["active_hosts"], 513)
        self.assertEqual(host["total_storage_bytes"], 7730000000000000)
        # used = total - remaining = 7.73 PB - 4.97 PB = 2.76 PB
        self.assertEqual(host["used_storage_bytes"], 2760000000000000)
        self.assertAlmostEqual(host["median_storage_sc_per_tb_month"],
                               1831.68, places=2)
        self.assertEqual(host["median_ingress_sc_per_tb"], 30.0)
        self.assertEqual(host["median_egress_sc_per_tb"], 3431.0)

    def test_block_metrics(self):
        block = parsers.parse_siascan_block(load("siascan_block_metrics.json"))
        self.assertEqual(block["network_height"], 602209)
        self.assertEqual(block["active_contracts"], 14856)

    def test_exchange_rate(self):
        self.assertEqual(
            parsers.parse_exchange_rate(load("siascan_exchange_rate.json")),
            0.000058)
        self.assertEqual(parsers.parse_exchange_rate({"rate": "0.5"}), 0.5)
        self.assertIsNone(parsers.parse_exchange_rate({"nope": 1}))
        self.assertIsNone(parsers.parse_exchange_rate(None))


class TestPlexParsers(unittest.TestCase):
    def test_identity(self):
        identity = parsers.parse_plex_identity(load("plex_root.json"))
        self.assertEqual(identity["name"], "RuikisFlix")
        self.assertEqual(identity["version"], "1.42.2.10156")

    def test_sessions_modes_and_bandwidth(self):
        result = parsers.parse_plex_sessions(load("plex_sessions.json"))
        self.assertEqual(result["count"], 2)
        # 24,500 + 8,000 kbps = 32.5 Mbps
        self.assertEqual(result["bandwidth_mbps"], 32.5)
        first, second = result["sessions"]
        self.assertEqual(first["mode"], "Direct Play")
        self.assertEqual(first["user"], "Liz")
        self.assertEqual(first["title"], "Dune: Part Two")
        self.assertEqual(second["mode"], "Transcode")
        self.assertEqual(second["title"], "The Office — Dinner Party")

    def test_sections_and_count(self):
        sections = parsers.parse_plex_sections(load("plex_sections.json"))
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0]["title"], "Movies HD / 4K")
        self.assertIsNone(sections[0]["count"])
        self.assertEqual(
            parsers.parse_plex_section_count(load("plex_section_all.json")),
            1234)


if __name__ == "__main__":
    unittest.main()
