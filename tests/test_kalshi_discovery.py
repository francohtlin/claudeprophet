from __future__ import annotations

import unittest

from kalshi_discovery.discovery import dedupe_raw, normalize_for_discovery, normalize_status_filter, normalized_market_matches


class KalshiDiscoveryTests(unittest.TestCase):
    def test_status_all_omits_filter(self) -> None:
        self.assertIsNone(normalize_status_filter("all"))
        self.assertEqual(normalize_status_filter("open"), "open")

    def test_dedupes_by_ticker(self) -> None:
        records = [{"ticker": "A"}, {"ticker": "A"}, {"ticker": "B"}]
        self.assertEqual([item["ticker"] for item in dedupe_raw(records)], ["A", "B"])

    def test_matches_normalized_market(self) -> None:
        market = {"question": "Will the NBA Finals winner be the Thunder?", "rules": "NBA championship market."}
        self.assertTrue(normalized_market_matches(market, ["nba", "finals"]))
        self.assertFalse(normalized_market_matches(market, ["bitcoin", "price"]))

    def test_discovery_normalization_exposes_ticker_without_dedupe(self) -> None:
        market = normalize_for_discovery({"ticker": "KXTEST", "title": "Will test pass?", "status": "active"})
        self.assertEqual(market["ticker"], "KXTEST")
        self.assertNotIn("_dedupe", market)


if __name__ == "__main__":
    unittest.main()
