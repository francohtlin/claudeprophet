"""Forecast the chosen Kalshi weather ladders -> open_weather_claudeprophet.jsonl.

Parallel to forecast_kpi/forecast_pm (ladder path). Each metric gets a fresh
`claude -p` child (subscription auth, no API key) that researches climatology,
seasonal outlooks and any current forecasts, returns median/p10/p90 for the
actual value, and we price every threshold  P(>= t) = 1 - Phi((t-median)/sigma).
"""

import json, subprocess, re, math, os, sys, argparse, pathlib
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from forecasting.weather_tracks import cfg

_ap = argparse.ArgumentParser()
_ap.add_argument("--track", default="wealthsimple", choices=["wealthsimple", "nearterm"])
_c = cfg(_ap.parse_args().track)
chosen = json.load(open(_c["chosen"]))
outp = _c["fcst"]
TODAY = date.today().isoformat()

def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

env = dict(os.environ)
env.pop("ANTHROPIC_API_KEY", None); env.pop("ANTHROPIC_AUTH_TOKEN", None)


def extract(s):
    i = s.find("{")
    if i < 0: return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{": depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0: return s[i:j + 1]
    return None


def prompt(c):
    return f"""You are ClaudeProphet, a live forecasting agent. Today is {TODAY}.
Forecast the actual reported value for this weather/climate market: "{c['question']}"
(subject: {c['co']}, period: {c['period']}, resolves ~{c['close'][:10]}).
Use web search for: climatological normals for this location/measure and period, the latest seasonal/monthly outlook (e.g. NOAA CPC, hurricane-season forecasts), and any current conditions or forecasts that bear on the final figure.
Give a calibrated distribution for the actual value that will be reported.
The market's current implied central estimate is about {c['market_median']:g} — do NOT just copy it; form your own view.
Respond with STRICT JSON only. Plain numbers, no commas/units:
{{"median": <number>, "p10": <number>, "p90": <number>, "reasoning": "<=3 sentences", "key_evidence": ["...","..."]}}"""


with outp.open("a") as _:
    pass

for c in chosen:
    try:
        res = subprocess.run(
            ["claude", "-p", prompt(c), "--model", "claude-opus-4-8", "--max-turns", "40",
             "--dangerously-skip-permissions", "--output-format", "json"],
            capture_output=True, text=True, env=env, timeout=900)
        d = json.loads(res.stdout)
        f = json.loads(re.sub(r"(?<=\d),(?=\d)", "", extract(d.get("result", "")) or "{}"))
        med, p10, p90 = float(f["median"]), float(f["p10"]), float(f["p90"])
        sigma = max(1e-9, (p90 - p10) / 2.5631)
        thr = [{"t": t, "cp_p": round(1 - ncdf((t - med) / sigma), 3)} for t in c["thresholds"]]
        rec = {**c, "cp_median": med, "cp_p10": p10, "cp_p90": p90, "cp_thresholds": thr,
               "reasoning": f.get("reasoning", ""), "evidence": f.get("key_evidence", []),
               "cost_usd": d.get("total_cost_usd")}
        summary = f"median={med:g}"
    except Exception as e:
        rec = {**c, "error": str(e)[:200], "stdout_tail": (res.stdout[-300:] if 'res' in dir() else "")}
        summary = f"ERROR {str(e)[:60]}"
    with outp.open("a") as f2:
        f2.write(json.dumps(rec) + "\n")
    print(f"done: {c['co']} ({c['period']})  {summary}  cost={rec.get('cost_usd')}", flush=True)

print("ALL DONE", flush=True)
