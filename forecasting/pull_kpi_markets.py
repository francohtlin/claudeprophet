"""Pull all currently-open Kalshi company-KPI markets -> data/company_kpi_open.jsonl.

Enumerates every KPI-tagged series in the Companies/Financials categories and
records each open market's question, resolution date, current mid-price, and volume.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.kalshi import list_markets, list_series


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    kpi: dict[str, str] = {}
    for cat in ("Companies", "Financials"):
        for s in list_series(cat):
            if "kpi" in str(s.get("title") or "").lower() and s.get("ticker"):
                kpi[s["ticker"]] = s.get("title")

    rows = []
    for st, title in kpi.items():
        ms = []
        for _ in range(3):
            try:
                ms = list_markets(st, status="open", max_markets=2000)
                break
            except Exception:
                time.sleep(0.4)
        for m in ms:
            yb, ya = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
            mid = round((yb + ya) / 2, 3) if (yb is not None and ya is not None) else None
            rows.append({
                "company": title, "series_ticker": st, "ticker": m.get("ticker"),
                "question": m.get("title"), "close_time": m.get("close_time"),
                "yes_mid": mid, "volume": _num(m.get("volume_fp")),
            })
    rows = [r for r in rows if r["ticker"] and r["question"]]
    rows.sort(key=lambda r: (r.get("close_time") or "", r["company"]))

    out = Path(__file__).resolve().parents[1] / "data" / "company_kpi_open.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} open KPI markets across {len(kpi)} series -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
