from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from market_lookup.dedupe import dedupe_markets
from market_lookup.lookup import annotate_close_time, attach_kalshi_history, interleave_sources, order_by_close_time_fit
from market_lookup.providers.kalshi_history import (
    endpoint_segments,
    extract_candlesticks,
    normalize_candlestick,
    normalize_trade,
)
from market_lookup.providers.kalshi import category_hints, kalshi_market_matches, normalize_kalshi_market, series_hints
from market_lookup.providers.polymarket_gamma import (
    market_matches_query,
    meaningful_terms,
    min_required_hits,
    normalize_market,
    term_hits,
)


class MarketLookupTests(unittest.TestCase):
    def test_normalized_market_hides_ids_in_public_fields(self) -> None:
        market = normalize_market(
            {
                "id": "123",
                "conditionId": "0xabc",
                "slug": "will-example-happen",
                "question": "Will example happen?",
                "description": "This market resolves yes if example happens.\n\nMore rules.",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.40", "0.60"]',
                "bestBid": "0.39",
                "bestAsk": "0.41",
                "active": True,
                "closed": False,
                "endDate": "2026-01-01T00:00:00Z",
                "volume": "1000",
            },
            parent_event=None,
        )
        self.assertEqual(market["source"], "Polymarket")
        self.assertEqual(market["status"], "open")
        self.assertEqual(market["prices"]["yes_bid"], 0.39)
        self.assertEqual(market["prices"]["no_ask"], 0.61)
        self.assertEqual(market["description"], "This market resolves yes if example happens.")
        self.assertNotIn("url", market)
        self.assertNotIn("market_id", market)
        self.assertNotIn("canonical_key", market)

    def test_dedupes_same_condition_id_across_sources(self) -> None:
        markets = [
            {"source": "Polymarket Gamma", "question": "A", "_dedupe": {"condition_id": "0xabc"}},
            {"source": "PMXT", "question": "A duplicate", "_dedupe": {"condition_id": "0xabc"}},
        ]
        self.assertEqual(len(dedupe_markets(markets)), 1)

    def test_meaningful_terms_removes_stop_words(self) -> None:
        self.assertIn("powell", meaningful_terms("Will Powell say tariff during June?"))
        self.assertNotIn("will", meaningful_terms("Will Powell say tariff during June?"))

    def test_annotates_close_time_against_ideal_close_time(self) -> None:
        markets = [
            {"question": "old", "close_time": "2026-01-01T00:00:00Z"},
            {"question": "late", "close_time": "2026-07-01T00:00:00Z"},
            {"question": "unknown", "close_time": None},
        ]
        annotated = annotate_close_time(markets, "2026-06-01T00:00:00Z")
        self.assertTrue(annotated[0]["closes_before_ideal_close_time"])
        self.assertFalse(annotated[1]["closes_before_ideal_close_time"])
        self.assertIsNone(annotated[2]["closes_before_ideal_close_time"])

    def test_orders_non_stale_markets_before_stale_markets(self) -> None:
        markets = [
            {"question": "stale", "closes_before_ideal_close_time": True},
            {"question": "current", "closes_before_ideal_close_time": False},
            {"question": "unknown", "closes_before_ideal_close_time": None},
        ]
        ordered = order_by_close_time_fit(markets)
        self.assertEqual([item["question"] for item in ordered], ["current", "unknown", "stale"])

    def test_orders_open_liquid_markets_before_closed_markets(self) -> None:
        markets = [
            {"question": "closed", "status": "closed", "closes_before_ideal_close_time": False},
            {
                "question": "open liquid",
                "status": "open",
                "closes_before_ideal_close_time": True,
                "liquidity": {"liquidity": 1000},
            },
            {
                "question": "open empty",
                "status": "open",
                "closes_before_ideal_close_time": True,
                "liquidity": {"liquidity": 0},
            },
        ]
        ordered = order_by_close_time_fit(markets)
        self.assertEqual([item["question"] for item in ordered], ["open liquid", "open empty", "closed"])

    def test_interleaves_sources_without_adding_rank(self) -> None:
        markets = [
            {"source": "Polymarket", "question": "p1"},
            {"source": "Polymarket", "question": "p2"},
            {"source": "Kalshi", "question": "k1"},
        ]
        ordered = interleave_sources(markets)
        self.assertEqual([item["question"] for item in ordered], ["p1", "k1", "p2"])
        self.assertNotIn("rank", ordered[0])

    def test_term_hits_match_tokens_not_substrings(self) -> None:
        self.assertEqual(term_hits("greatest hits album", ["rates"]), 0)
        self.assertEqual(term_hits("interest rates decision", ["rates"]), 1)

    def test_rich_queries_require_more_token_hits(self) -> None:
        self.assertEqual(min_required_hits("Will Bitcoin be above 100000 on May 17 2026?"), 3)
        self.assertEqual(min_required_hits("Will Powell resign?"), 2)

    def test_numeric_query_requires_matching_year(self) -> None:
        query = "Who will win the 2028 United States presidential election?"
        self.assertFalse(
            market_matches_query(
                {"question": "Will David McCormick win the 2022 United States Senate election in Pennsylvania?"},
                query,
            )
        )
        self.assertTrue(
            market_matches_query(
                {"question": "Who will win the 2028 US Presidential Election?"},
                query,
            )
        )

    def test_large_numeric_query_requires_exact_large_number(self) -> None:
        query = "Will Bitcoin reach 200000 by December 31 2026?"
        self.assertFalse(
            market_matches_query(
                {"question": "Will Bitcoin reach $100,000 by December 31, 2026?"},
                query,
            )
        )
        self.assertTrue(
            market_matches_query(
                {"question": "Will Bitcoin reach $200,000 by December 31, 2026?"},
                query,
            )
        )

    def test_required_domain_terms_block_near_misses(self) -> None:
        self.assertFalse(
            market_matches_query(
                {"question": "Will China's annual inflation in 2026 be above 3 percent?"},
                "Will CPI inflation be above 3 percent in June 2026?",
            )
        )
        self.assertFalse(
            market_matches_query(
                {"question": "Will China invade Taiwan before GTA VI?"},
                "Which party will control the House after the 2026 election?",
            )
        )
        self.assertFalse(
            market_matches_query(
                {"question": "Will the Cleveland Cavaliers win the 2026 NBA Finals?"},
                "Will the Oklahoma City Thunder win the 2026 NBA Finals?",
            )
        )
        self.assertTrue(
            market_matches_query(
                {"question": "Will the Oklahoma City Thunder win the 2026 NBA Finals?"},
                "Will the Oklahoma City Thunder win the 2026 NBA Finals?",
            )
        )

    def test_normalizes_kalshi_market(self) -> None:
        market = normalize_kalshi_market(
            {
                "ticker": "KXFED-TEST",
                "title": "Fed Decision in June?",
                "yes_sub_title": "Fed decreases rates by 25 bps",
                "rules_primary": "Resolves yes if the Fed decreases rates by 25 bps.",
                "status": "active",
                "close_time": "2026-06-18T00:00:00Z",
                "expiration_time": "2026-06-19T00:00:00Z",
                "yes_bid_dollars": "0.4200",
                "yes_ask_dollars": "0.4500",
                "no_bid_dollars": "0.5500",
                "no_ask_dollars": "0.5800",
                "last_price_dollars": "0.4300",
                "volume_fp": "123.00",
                "volume_24h_fp": "12.00",
                "liquidity_dollars": "456.00",
                "open_interest_fp": "78.00",
            }
        )
        self.assertEqual(market["source"], "Kalshi")
        self.assertEqual(market["status"], "open")
        self.assertEqual(market["prices"]["yes_bid"], 0.42)
        self.assertEqual(market["prices"]["spread"], 0.03)
        self.assertIn("Fed decreases", market["question"])

    def test_kalshi_market_text_filter(self) -> None:
        market = {
            "title": "Fed Decision in June?",
            "yes_sub_title": "Will the Fed decrease interest rates by 25 bps?",
            "rules_primary": "Resolves yes if the Federal Reserve decreases rates.",
            "ticker": "KXFED-TEST",
        }
        self.assertTrue(kalshi_market_matches(market, meaningful_terms("Will the Fed cut rates in June 2026?")))
        self.assertFalse(kalshi_market_matches(market, meaningful_terms("Will Bitcoin hit 200000 in 2026?")))

    def test_kalshi_series_hints_from_text(self) -> None:
        self.assertIn("KXFED", series_hints("Will the Fed cut rates in June 2026?"))
        self.assertIn("KXBTC", series_hints("Will Bitcoin hit 200000 in 2026?"))

    def test_kalshi_category_hints_from_text(self) -> None:
        self.assertIn("Elections", category_hints("Who will win the 2028 presidential election?"))
        self.assertIn("Sports", category_hints("Will the Lakers win the NBA finals?"))
        self.assertIn("Crypto", category_hints("Will Bitcoin hit 200000 in 2026?"))
        self.assertIn("Economics", category_hints("Will CPI inflation be above 3% in June 2026?"))

    def test_kalshi_history_endpoint_segments_split_around_cutoff(self) -> None:
        start = datetime(2026, 3, 16, 0, 0, tzinfo=timezone.utc)
        cutoff = datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc)
        segments = endpoint_segments(
            live_endpoint="live",
            historical_endpoint="historical",
            start=start,
            end=end,
            cutoff=cutoff,
        )
        self.assertEqual(
            segments,
            [
                ("historical", start, cutoff),
                ("live", cutoff, end),
            ],
        )

    def test_kalshi_history_normalizes_trades_without_ids(self) -> None:
        trade = normalize_trade(
            {
                "trade_id": "secret-trade-id",
                "ticker": "KXTEST",
                "created_time": "2026-03-16T23:59:59Z",
                "yes_price_dollars": "0.2500",
                "no_price_dollars": "0.7500",
                "count_fp": "10.00",
                "taker_side": "yes",
                "taker_book_side": "bid",
            }
        )
        self.assertEqual(trade["yes_price"], 0.25)
        self.assertEqual(trade["count"], 10.0)
        self.assertNotIn("trade_id", trade)
        self.assertNotIn("ticker", trade)

    def test_kalshi_history_extracts_and_normalizes_live_candles(self) -> None:
        candles = extract_candlesticks(
            {
                "markets": [
                    {
                        "market_ticker": "KXTEST",
                        "candlesticks": [
                            {
                                "end_period_ts": 1778914800,
                                "price": {"close_dollars": "0.5100", "previous_dollars": "0.4900"},
                                "volume_fp": "0.67",
                                "open_interest_fp": "30835.08",
                            }
                        ],
                    }
                ]
            }
        )
        normalized = normalize_candlestick(candles[0])
        self.assertEqual(normalized["price"]["close"], 0.51)
        self.assertEqual(normalized["volume"], 0.67)
        self.assertNotIn("market_ticker", normalized)

    def test_attach_kalshi_history_uses_private_ticker_but_does_not_expose_it(self) -> None:
        markets = [
            {
                "source": "Kalshi",
                "question": "Will example happen?",
                "_dedupe": {"market_id": "KXTEST"},
            }
        ]
        with patch("market_lookup.lookup.fetch_kalshi_market_history") as fetch:
            fetch.return_value = {"trades": [{"yes_price": 0.4}], "candlesticks": []}
            enriched = attach_kalshi_history(markets, lookback_days=7, trade_limit=5, candle_limit=3)
        fetch.assert_called_once_with("KXTEST", lookback_days=7, trade_limit=5, candle_limit=3)
        self.assertEqual(enriched[0]["history"]["trades"][0]["yes_price"], 0.4)


if __name__ == "__main__":
    unittest.main()
