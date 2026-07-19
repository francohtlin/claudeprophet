from __future__ import annotations

import unittest

from local_env import configure_certifi, strip_quotes
from finance_lookup.lookup import (
    extract_symbols,
    infer_macro_series,
    normalize_crypto_symbol,
    parse_symbols,
    route_asset_type,
    safe_float,
    summarize_checks,
)


class FinanceLookupTests(unittest.TestCase):
    def test_parse_symbols_dedupes_and_uppercases(self) -> None:
        self.assertEqual(parse_symbols("nvda, NVDA, btc"), ["NVDA", "BTC"])

    def test_extract_symbols_from_query(self) -> None:
        self.assertEqual(extract_symbols("Will NVDA close above 120 after earnings?"), ["NVDA"])

    def test_extracts_crypto_aliases(self) -> None:
        self.assertIn("BTC", extract_symbols("Will bitcoin trade above 100000?"))

    def test_normalizes_crypto_pair(self) -> None:
        self.assertEqual(normalize_crypto_symbol("btc"), "BTC/USDT")
        self.assertEqual(normalize_crypto_symbol("ETHUSD"), "ETH/USDT")
        self.assertEqual(normalize_crypto_symbol("SOL/USDT"), "SOL/USDT")

    def test_auto_routes_crypto(self) -> None:
        self.assertEqual(route_asset_type("BTC", "auto"), "crypto")
        self.assertEqual(route_asset_type("NVDA", "auto"), "equity")

    def test_infers_macro_series(self) -> None:
        self.assertIn("CPIAUCSL", infer_macro_series("Will CPI inflation rise?"))
        self.assertIn("UNRATE", infer_macro_series("Will unemployment increase?"))

    def test_safe_float_rejects_nan(self) -> None:
        self.assertIsNone(safe_float("nan"))
        self.assertEqual(safe_float("1.25"), 1.25)

    def test_summarize_checks(self) -> None:
        self.assertEqual(
            summarize_checks([{"status": "pass"}, {"status": "skipped"}, {"status": "fail"}]),
            {"pass": 1, "fail": 1, "skipped": 1},
        )

    def test_strip_quotes(self) -> None:
        self.assertEqual(strip_quotes('"abc def"'), "abc def")
        self.assertEqual(strip_quotes("plain"), "plain")

    def test_configure_certifi_sets_bundle(self) -> None:
        configure_certifi()


if __name__ == "__main__":
    unittest.main()
