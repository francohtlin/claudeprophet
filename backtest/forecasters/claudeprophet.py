"""Agent forecaster: POST each case's event to a running ClaudeProphet /predict.

NOT leakage-safe. ClaudeProphet uses live web search, so for a market that has
already resolved it can simply retrieve the outcome. Treat agent-mode results as
an *upper bound contaminated by hindsight*, not a clean forecasting score, unless
you run ClaudeProphet in a point-in-time / search-disabled configuration. See the
README "Lookahead bias" section.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from backtest.data import Case


class ClaudeProphetForecaster:
    name = "claudeprophet"
    leakage_safe = False

    def __init__(
        self,
        *,
        endpoint: str = "http://127.0.0.1:8080",
        timeout: float = 600.0,
        as_of_note: bool = True,
        **_ignored,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.as_of_note = as_of_note

    def predict_all(self, cases: list[Case]) -> list[float | None]:
        return [self._predict_one(case) for case in cases]

    def _predict_one(self, case: Case) -> float | None:
        event = case.event()
        if self.as_of_note:
            # Weak mitigation: ask the agent to reason as-of the pre-close date and
            # ignore anything published afterward. Does not truly sandbox search.
            event["forecast_as_of"] = case.close_time
            event["instructions"] = (
                "Forecast as of forecast_as_of. Ignore any information published "
                "after that timestamp; do not look up the realized outcome."
            )
        try:
            resp = self._post("/predict", event)
        except Exception:
            return None
        return _yes_probability(resp)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def _yes_probability(response: dict[str, Any]) -> float | None:
    probs = response.get("probabilities")
    if not isinstance(probs, list):
        return None
    for row in probs:
        if isinstance(row, dict) and str(row.get("market", "")).strip().lower() == "yes":
            value = row.get("probability")
            if isinstance(value, (int, float)):
                return max(0.0, min(1.0, float(value)))
    return None
