"""Walk-forward base-rate forecaster (leakage-safe).

For each case, predict the empirical YES frequency of *prior* resolved markets in
the same series (those that closed strictly earlier). This is the honest skill
floor for any forecaster on recurring markets.
"""

from __future__ import annotations

from backtest.data import Case


class BaseRateForecaster:
    name = "base_rate"
    leakage_safe = True

    def __init__(self, *, prior: float = 0.5, min_history: int = 1, **_ignored) -> None:
        self.prior = prior
        self.min_history = min_history

    def predict_all(self, cases: list[Case]) -> list[float | None]:
        # cases are already sorted by close_ts in build_cases; be defensive anyway.
        order = sorted(range(len(cases)), key=lambda i: cases[i].close_ts)
        counts: dict[str, list[int]] = {}  # series -> [yes, total] among prior closes
        preds: list[float | None] = [None] * len(cases)
        for i in order:
            case = cases[i]
            yes, total = counts.get(case.series_ticker, [0, 0])
            preds[i] = (yes / total) if total >= self.min_history else self.prior
            counts[case.series_ticker] = [yes + case.outcome, total + 1]
        return preds
