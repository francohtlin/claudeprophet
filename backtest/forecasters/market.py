"""Market-price forecaster (leakage-safe when snapshot precedes close).

Uses the market's own YES price captured ``snapshot_days`` before resolution.
This is the benchmark every model must beat: an efficient market is hard to
out-forecast. Abstains (None) when no pre-close snapshot was available.
"""

from __future__ import annotations

from backtest.data import Case


class MarketPriceForecaster:
    name = "market"
    leakage_safe = True

    def __init__(self, **_ignored) -> None:
        pass

    def predict_all(self, cases: list[Case]) -> list[float | None]:
        return [c.snapshot_yes_price for c in cases]
