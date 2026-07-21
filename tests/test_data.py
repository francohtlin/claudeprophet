from __future__ import annotations

import unittest

from backtest.data import market_to_case, parse_iso_ts, _parse_threshold


class DataTests(unittest.TestCase):
    def test_parse_iso_ts(self) -> None:
        self.assertEqual(parse_iso_ts("1970-01-01T00:00:00Z"), 0)
        self.assertIsNone(parse_iso_ts(None))
        self.assertIsNone(parse_iso_ts("not-a-date"))

    def test_market_to_case_resolved_yes(self) -> None:
        raw = {
            "ticker": "KXCPIYOY-26JUL-4.5",
            "series_ticker": "KXCPIYOY",
            "title": "Will CPI inflation be above 4.5% for July 2026?",
            "close_time": "2026-07-14T12:29:00Z",
            "result": "yes",
        }
        case = market_to_case(raw)
        assert case is not None
        self.assertEqual(case.outcome, 1)
        self.assertEqual(case.series_ticker, "KXCPIYOY")
        self.assertEqual(case.meta["threshold"], 4.5)

    def test_market_to_case_skips_unresolved(self) -> None:
        self.assertIsNone(market_to_case({"ticker": "X", "result": "", "close_time": "2026-01-01T00:00:00Z"}))
        self.assertIsNone(market_to_case({"ticker": "X", "result": "yes"}))  # no close_time

    def test_series_inferred_from_ticker_when_absent(self) -> None:
        case = market_to_case(
            {"ticker": "KXPAYROLLS-26JUN-175000", "title": "jobs", "result": "no", "close_time": "2026-07-02T12:29:00Z"}
        )
        assert case is not None
        self.assertEqual(case.series_ticker, "KXPAYROLLS")

    def test_threshold_parse(self) -> None:
        self.assertEqual(_parse_threshold("Will above 175,000 jobs be added?"), 175000.0)
        self.assertIsNone(_parse_threshold("Who wins the game?"))


if __name__ == "__main__":
    unittest.main()
