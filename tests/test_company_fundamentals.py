from __future__ import annotations

import unittest

from company_fundamentals.fundamentals import (
    extract_symbols_from_text,
    filter_kpi_markets,
    is_kpi_market,
    market_kpi_signals,
    parse_categories,
    parse_symbols,
    query_terms,
    resolve_symbols,
    series_matches,
    symbols_from_series,
)


class CompanyFundamentalsTests(unittest.TestCase):
    def test_kpi_signals_detects_company_metrics(self) -> None:
        market = {"question": "Will Nvidia Q3 revenue beat guidance?", "rules": "Resolves on reported revenue."}
        signals = market_kpi_signals(market)
        self.assertIn("revenue", signals)
        self.assertIn("guidance", signals)
        self.assertTrue(is_kpi_market(market))

    def test_non_kpi_market_is_excluded(self) -> None:
        market = {"question": "Will it rain in NYC on Friday?", "rules": "Weather market."}
        self.assertEqual(market_kpi_signals(market), [])
        self.assertFalse(is_kpi_market(market))

    def test_filter_keeps_only_kpi_markets_and_annotates(self) -> None:
        markets = [
            {"ticker": "A", "question": "Tesla deliveries above 500k this quarter?"},
            {"ticker": "B", "question": "Who wins the Super Bowl?"},
        ]
        kept = filter_kpi_markets(markets)
        self.assertEqual([m["ticker"] for m in kept], ["A"])
        self.assertIn("deliveries", kept[0]["kpi_signals"])

    def test_symbol_extraction_prefers_alias_map(self) -> None:
        self.assertEqual(extract_symbols_from_text("Will Apple beat earnings?"), ["AAPL"])

    def test_symbol_extraction_ignores_common_uppercase_words(self) -> None:
        # YES/NO/EPS are stopwords; only NVDA should survive.
        self.assertEqual(extract_symbols_from_text("YES NVDA EPS NO"), ["NVDA"])

    def test_explicit_symbols_win_over_inference(self) -> None:
        resolved = resolve_symbols(
            parse_symbols("MSFT"),
            query="Will Apple beat earnings?",
            markets=[{"question": "Tesla deliveries?"}],
        )
        self.assertEqual(resolved, ["MSFT"])

    def test_resolution_falls_back_to_query_then_markets(self) -> None:
        from_query = resolve_symbols([], query="Nvidia revenue?", markets=[{"question": "Tesla deliveries?"}])
        self.assertEqual(from_query, ["NVDA"])

        from_markets = resolve_symbols([], query="", markets=[{"question": "Tesla deliveries above 500k?"}])
        self.assertEqual(from_markets, ["TSLA"])

    def test_parse_symbols_normalizes_and_dedupes(self) -> None:
        self.assertEqual(parse_symbols("nvda, AAPL nvda"), ["NVDA", "AAPL"])

    def test_query_terms_drops_stopwords(self) -> None:
        self.assertEqual(query_terms("Will Nvidia report revenue in Q3"), ["nvidia", "revenue"])

    def test_series_matches_by_term_or_symbol(self) -> None:
        series = {"ticker": "KXPLTR", "title": "Palantir Technologies quarterly customers"}
        self.assertTrue(series_matches(series, ["palantir"], []))
        self.assertTrue(series_matches(series, [], ["PLTR"]))
        self.assertFalse(series_matches(series, ["tesla"], ["TSLA"]))

    def test_series_matches_requires_a_filter(self) -> None:
        self.assertFalse(series_matches({"ticker": "KXPLTR", "title": "Palantir"}, [], []))

    def test_symbols_from_series_uses_title_alias(self) -> None:
        series = [
            {"ticker": "KXMETA", "title": "Meta Platforms headcount"},
            {"ticker": "KXHOOD", "title": "Robinhood funded customers"},
        ]
        self.assertEqual(symbols_from_series(series), ["META", "HOOD"])

    def test_resolve_falls_back_to_series_titles(self) -> None:
        resolved = resolve_symbols(
            [], query="", markets=[], series_list=[{"ticker": "KXHOOD", "title": "Robinhood funded customers"}]
        )
        self.assertEqual(resolved, ["HOOD"])

    def test_parse_categories_defaults(self) -> None:
        self.assertEqual(parse_categories(""), ["Companies", "Financials"])
        self.assertEqual(parse_categories("Companies, Crypto"), ["Companies", "Crypto"])


if __name__ == "__main__":
    unittest.main()
