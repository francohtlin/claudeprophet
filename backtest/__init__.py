"""claudeprophet-backtest: walk-forward backtesting for Kalshi binary markets.

Pulls historical *resolved* (settled) Kalshi markets, generates forecasts with one
or more pluggable forecasters, and scores them against the realized outcome. The
default forecasters are leakage-safe (walk-forward); the agent forecaster that
calls ClaudeProphet is opt-in and lookahead-caveated (see README).
"""

__version__ = "0.1.0"
