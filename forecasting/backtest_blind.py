"""Blind (no-search) forecasts for RESOLVED company-KPI metrics — the backtest leg.

Leakage-free by construction: WebSearch/WebFetch are disallowed and the model's
training cutoff (~Jan 2026) predates every outcome here (Apr–Jul 2026 reports).
Each resolved ladder is collapsed to one latent-metric forecast made "as of" a
week before the market closed.

Output: data/forecasts/backtest_blind.jsonl
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import parse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "company_kpi_full.jsonl"
OUT = ROOT / "data" / "forecasts" / "backtest_blind.jsonl"


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def extract_obj(s: str) -> str | None:
    i = s.find("{")
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
    return None


def getnum(key: str, s: str) -> float | None:
    m = re.search(r'"' + key + r'"\s*:\s*\$?([\d,\.]+)', s)
    return float(m.group(1).replace(",", "")) if m else None


def build_groups() -> list[dict]:
    rows = [json.loads(l) for l in SRC.open()]
    groups: dict[tuple, dict] = defaultdict(lambda: {"contracts": []})
    for r in rows:
        p = parse(r.get("question", ""))
        if not p:
            continue
        val, metric, period = p
        co = (r.get("series_title") or "").replace(" KPI", "").strip()
        key = (co, metric, period, (r.get("close_time") or "")[:10])
        g = groups[key]
        g["co"], g["metric"], g["period"], g["resolves"] = key
        g["contracts"].append({
            "ticker": r["ticker"], "threshold": val,
            "result": str(r.get("result")).lower(), "question": r["question"],
        })
    out = [g for g in groups.values() if len(g["contracts"]) >= 3]
    out.sort(key=lambda g: g["resolves"])
    return out


def main() -> int:
    groups = build_groups()
    done = set()
    if OUT.exists():
        for l in OUT.open():
            r = json.loads(l)
            done.add((r["co"], r["metric"], r["period"], r["resolves"]))
    todo = [g for g in groups if (g["co"], g["metric"], g["period"], g["resolves"]) not in done]
    print(f"{len(groups)} resolved metric groups; {len(todo)} to forecast", flush=True)

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    for g in todo:
        close = datetime.fromisoformat(g["resolves"])
        asof = (close - timedelta(days=7)).date().isoformat()
        thr = sorted(c["threshold"] for c in g["contracts"])
        prompt = f"""You are a probabilistic forecaster. It is {asof}. {g['co']} has NOT yet reported "{g['metric']}" for {g['period']}. You have NO web access — reason only from prior knowledge as of your training data. Do not claim to look anything up and do not state any actual reported figure for {g['period']}.

Forecast the value {g['co']} will report for "{g['metric']}" in {g['period']}. Give a calibrated distribution. For scale: prediction-market threshold contracts on this figure range from {thr[0]:,.0f} to {thr[-1]:,.0f} (same units as the reported figure) — your answer must be in these units, though it need not fall inside that range.

Respond with STRICT JSON only. Numbers as plain integers, NO commas or units:
{{"median": <number>, "p10": <number>, "p90": <number>, "reasoning": "<=2 sentences"}}"""
        res = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-opus-4-8",
             "--disallowedTools", "WebSearch,WebFetch", "--output-format", "json"],
            capture_output=True, text=True, env=env, timeout=300,
        )
        rec: dict
        try:
            d = json.loads(res.stdout)
            r = d.get("result", "")
            try:
                f = json.loads(re.sub(r"(?<=\d),(?=\d)", "", extract_obj(r) or ""))
                med, p10, p90 = float(f["median"]), float(f["p10"]), float(f["p90"])
                reason = f.get("reasoning", "")
            except Exception:
                med, p10, p90 = getnum("median", r), getnum("p10", r), getnum("p90", r)
                if None in (med, p10, p90):
                    raise ValueError("number extract failed")
                reason = ""
            sigma = max(1e-9, (p90 - p10) / 2.5631)
            contracts = [
                {**c, "cp_p": round(1 - ncdf((c["threshold"] - med) / sigma), 3)}
                for c in g["contracts"]
            ]
            rec = {"co": g["co"], "metric": g["metric"], "period": g["period"],
                   "resolves": g["resolves"], "asof": asof,
                   "cp_median": med, "cp_p10": p10, "cp_p90": p90,
                   "reasoning": reason, "contracts": contracts,
                   "cost_usd": d.get("total_cost_usd")}
        except Exception as exc:
            rec = {"co": g["co"], "metric": g["metric"], "period": g["period"],
                   "resolves": g["resolves"], "error": str(exc)[:200]}
        with OUT.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"done: {g['co']} — {g['metric']} ({g['period']})  median={rec.get('cp_median')}", flush=True)
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
