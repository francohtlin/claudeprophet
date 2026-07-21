from __future__ import annotations

from typing import Protocol, runtime_checkable

from backtest.data import Case


@runtime_checkable
class Forecaster(Protocol):
    #: short identifier used in reports and CLI selection
    name: str

    #: True if the forecaster only uses pre-close information (no lookahead).
    leakage_safe: bool

    def predict_all(self, cases: list[Case]) -> list[float | None]:
        """Return one P(Yes) per case (same order). None = abstained/unavailable."""
        ...
