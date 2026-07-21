from __future__ import annotations

import unittest

from backtest.data import Case
from backtest.forecasters.base_rate import BaseRateForecaster
from backtest.forecasters.market import MarketPriceForecaster


def _case(ticker: str, series: str, close_ts: int, outcome: int, snap: float | None = None) -> Case:
    return Case(
        ticker=ticker,
        series_ticker=series,
        title=ticker,
        close_time="",
        close_ts=close_ts,
        result="yes" if outcome else "no",
        outcome=outcome,
        snapshot_yes_price=snap,
    )


class BaseRateForecasterTests(unittest.TestCase):
    def test_walk_forward_uses_only_prior_outcomes(self) -> None:
        cases = [
            _case("A", "S", 100, 1),
            _case("B", "S", 200, 1),
            _case("C", "S", 300, 0),
        ]
        preds = BaseRateForecaster(prior=0.5).predict_all(cases)
        # A: no history -> prior 0.5
        # B: prior [A=yes] -> 1.0
        # C: prior [A,B=yes] -> 1.0
        self.assertEqual(preds, [0.5, 1.0, 1.0])

    def test_no_lookahead_last_case_ignores_own_outcome(self) -> None:
        cases = [_case("A", "S", 100, 0), _case("B", "S", 200, 1)]
        preds = BaseRateForecaster(prior=0.5).predict_all(cases)
        self.assertEqual(preds, [0.5, 0.0])  # B sees only A=no -> 0.0

    def test_series_are_independent(self) -> None:
        cases = [_case("A", "S1", 100, 1), _case("B", "S2", 200, 0)]
        preds = BaseRateForecaster(prior=0.5).predict_all(cases)
        self.assertEqual(preds, [0.5, 0.5])  # S2 has no prior history

    def test_ordering_is_by_close_ts_not_list_order(self) -> None:
        # later-listed but earlier-closing case must be treated as prior history
        cases = [_case("B", "S", 200, 1), _case("A", "S", 100, 1)]
        preds = BaseRateForecaster(prior=0.5).predict_all(cases)
        # A (ts100) first -> prior 0.5 ; B (ts200) sees A=yes -> 1.0
        self.assertEqual(preds, [1.0, 0.5])


class MarketForecasterTests(unittest.TestCase):
    def test_passes_through_snapshot_and_abstains_when_missing(self) -> None:
        cases = [_case("A", "S", 100, 1, snap=0.62), _case("B", "S", 200, 0, snap=None)]
        self.assertEqual(MarketPriceForecaster().predict_all(cases), [0.62, None])


if __name__ == "__main__":
    unittest.main()
