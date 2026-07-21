"""Proper scoring rules and calibration for binary probabilistic forecasts.

Every function takes ``pairs``: a list of (p, y) where p = P(Yes) in [0,1] and
y = realized outcome in {0,1}.
"""

from __future__ import annotations

import math
from typing import Any


def _clip(p: float, eps: float = 1e-12) -> float:
    return min(1.0 - eps, max(eps, float(p)))


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    """Mean squared error of the probability. Lower is better (0 = perfect)."""
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]]) -> float | None:
    """Mean negative log-likelihood. Lower is better."""
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        q = _clip(p)
        total += -(y * math.log(q) + (1 - y) * math.log(1 - q))
    return total / len(pairs)


def accuracy(pairs: list[tuple[float, int]], *, threshold: float = 0.5) -> float | None:
    """Fraction where round(p) matches y (ties at threshold count as Yes)."""
    if not pairs:
        return None
    hits = sum(1 for p, y in pairs if (1 if p >= threshold else 0) == y)
    return hits / len(pairs)


def base_rate(pairs: list[tuple[float, int]]) -> float | None:
    """Empirical P(Yes) across the sample — the naive 'always predict base rate' skill floor."""
    if not pairs:
        return None
    return sum(y for _, y in pairs) / len(pairs)


def calibration_bins(pairs: list[tuple[float, int]], *, bins: int = 10) -> list[dict[str, Any]]:
    """Reliability table: predicted probability vs realized frequency per bin."""
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        idx = min(bins - 1, max(0, int(_clip(p) * bins)))
        buckets[idx].append((p, y))
    table: list[dict[str, Any]] = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / bins, (i + 1) / bins
        n = len(bucket)
        table.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "n": n,
                "mean_predicted": (sum(p for p, _ in bucket) / n) if n else None,
                "empirical_frequency": (sum(y for _, y in bucket) / n) if n else None,
            }
        )
    return table


def expected_calibration_error(pairs: list[tuple[float, int]], *, bins: int = 10) -> float | None:
    """Weighted mean gap between predicted probability and realized frequency."""
    if not pairs:
        return None
    total = 0.0
    for row in calibration_bins(pairs, bins=bins):
        if row["n"] and row["mean_predicted"] is not None:
            total += (row["n"] / len(pairs)) * abs(row["mean_predicted"] - row["empirical_frequency"])
    return total


def summarize(pairs: list[tuple[float, int]], *, bins: int = 10) -> dict[str, Any]:
    return {
        "n": len(pairs),
        "brier": brier_score(pairs),
        "log_loss": log_loss(pairs),
        "accuracy": accuracy(pairs),
        "ece": expected_calibration_error(pairs, bins=bins),
        "base_rate": base_rate(pairs),
        "mean_prediction": (sum(p for p, _ in pairs) / len(pairs)) if pairs else None,
        "calibration": calibration_bins(pairs, bins=bins),
    }
