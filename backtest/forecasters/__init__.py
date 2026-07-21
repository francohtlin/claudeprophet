"""Pluggable forecasters. Each maps a chronologically sorted list of Cases to a
list of P(Yes) predictions, one per case, computed *walk-forward* (a prediction
for case i may only use information available strictly before case i's close)."""

from backtest.forecasters.base import Forecaster
from backtest.forecasters.base_rate import BaseRateForecaster
from backtest.forecasters.market import MarketPriceForecaster
from backtest.forecasters.claudeprophet import ClaudeProphetForecaster

REGISTRY: dict[str, type[Forecaster]] = {
    "base_rate": BaseRateForecaster,
    "market": MarketPriceForecaster,
    "claudeprophet": ClaudeProphetForecaster,
}


def build(name: str, **kwargs) -> Forecaster:
    if name not in REGISTRY:
        raise KeyError(f"unknown forecaster {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name](**kwargs)
