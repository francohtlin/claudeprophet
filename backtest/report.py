"""Human-readable rendering of a backtest result."""

from __future__ import annotations

from typing import Any


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "  -  "
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def render_summary(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Backtest over {result['n_cases']} resolved markets")
    lines.append("")
    header = f"{'forecaster':16} {'cov':>5} {'brier':>8} {'logloss':>8} {'acc':>6} {'ece':>6} {'safe':>5}"
    lines.append(header)
    lines.append("-" * len(header))
    for name, m in result["metrics"].items():
        lines.append(
            f"{name:16} {m['coverage']:>5} "
            f"{_fmt(m['brier']):>8} {_fmt(m['log_loss']):>8} "
            f"{_fmt(m['accuracy'], 3):>6} {_fmt(m['ece'], 3):>6} "
            f"{('yes' if m['leakage_safe'] else 'NO'):>5}"
        )
    lines.append("")
    lines.append("Lower brier/logloss/ece is better. 'safe'=NO means the score may be")
    lines.append("contaminated by lookahead (see README). Compare models to 'market'.")
    return "\n".join(lines)


def render_cases(result: dict[str, Any], *, limit: int = 20) -> str:
    lines = ["", f"Sample forecasts (first {limit}):", ""]
    names = result["forecasters"]
    head = f"{'outcome':7} " + " ".join(f"{n[:10]:>10}" for n in names) + "  title"
    lines.append(head)
    lines.append("-" * min(len(head), 100))
    for rec in result["records"][:limit]:
        cols = " ".join(_fmt(rec["forecasts"][n], 3).rjust(10) for n in names)
        outcome = "YES" if rec["outcome"] == 1 else "no"
        lines.append(f"{outcome:7} {cols}  {rec['title'][:60]}")
    return "\n".join(lines)
