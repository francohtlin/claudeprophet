"""Kalshi company-KPI source.

SPIKE NOTE: to keep the existing, working pipeline untouched, this adapter reads
the already-pulled data/company_kpi_open.jsonl and maps it into the unified row
schema. The MVP would instead move the live-pull logic from
forecasting/pull_kpi_markets.py behind this same `discover()`, so both venues are
pulled uniformly. Behaviour is identical either way — this just avoids re-hitting
the Kalshi API and avoids editing the live pipeline during the spike.
"""

from __future__ import annotations

import json
from pathlib import Path

from forecasting.kpi_metrics import parse
from forecasting.sources.base import UnifiedRow

ROOT = Path(__file__).resolve().parents[2]
OPEN = ROOT / "data" / "company_kpi_open.jsonl"


def _market_url(ticker: str) -> str:
    if not ticker or ticker.count("-") < 2:
        return ""
    return f"https://kalshi.com/markets/{ticker.split('-')[0]}/{ticker.rsplit('-', 1)[0]}"


class KalshiSource:
    name = "kalshi"

    def __init__(self, path: Path | None = None):
        self.path = path or OPEN

    def discover(self) -> list[UnifiedRow]:
        if not self.path.exists():
            return []
        rows: list[UnifiedRow] = []
        for line in self.path.open():
            r = json.loads(line)
            co = (r.get("company") or "").replace(" KPI", "").strip()
            p = parse(r.get("question", ""))
            thr, metric, period = (p if p else (None, "", ""))
            rows.append({
                "source": "kalshi", "company": co, "metric": metric, "period": period,
                "question": r.get("question", ""), "threshold": thr,
                "yes_mid": r.get("yes_mid"), "volume": float(r.get("volume") or 0),
                "close_time": r.get("close_time", ""), "ticker": r.get("ticker", ""),
                "market_url": _market_url(r.get("ticker", "")),
                "outcome_kind": "threshold" if thr is not None else "binary",
            })
        return rows
