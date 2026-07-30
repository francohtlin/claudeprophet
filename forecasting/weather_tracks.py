"""Weather track configs. Two parallel weather books share one pipeline
(pull/select/forecast/portfolio) via `--track`:

  * wealthsimple - the CIRO-authorized climate set (settles >= 30 days out),
    tradeable on Wealthsimple Predict.
  * nearterm     - fast-resolving monthly totals (rain, storm/tornado counts)
    closing within ~12 days, for building a calibration record quickly. NOT
    Wealthsimple-tradeable; excludes near-deterministic same-day daily temps.

Default is 'wealthsimple' so existing behaviour is unchanged.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FC = DATA / "forecasts"

TRACKS = {
    "wealthsimple": {
        "label": "Wealthsimple-eligible",
        "open": DATA / "weather_open.jsonl",
        "chosen": FC / "_chosen_weather.json",
        "fcst": FC / "open_weather_claudeprophet.jsonl",
        "ledger": DATA / "weather_portfolio.json",
        "min_settle_days": 30, "max_settle_days": None, "monthly_only": False,
    },
    "nearterm": {
        "label": "near-term",
        "open": DATA / "weather_nt_open.jsonl",
        "chosen": FC / "_chosen_weather_nt.json",
        "fcst": FC / "open_weather_nt_claudeprophet.jsonl",
        "ledger": DATA / "weather_nt_portfolio.json",
        "min_settle_days": 1, "max_settle_days": 12, "monthly_only": True,
    },
}


def cfg(track: str) -> dict:
    if track not in TRACKS:
        raise SystemExit(f"unknown weather track {track!r}; choose from {list(TRACKS)}")
    return TRACKS[track]
