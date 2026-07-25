"""Forecast the chosen Polymarket KPI metrics -> open_pm_claudeprophet.jsonl.

Parallel to forecast_kpi.py. Two prompt paths:
  * ladder : ask for median/p10/p90 of the reported figure, fit a normal, price
             every threshold  P(>= t) = 1 - Phi((t-median)/sigma).
  * binary : ask directly for P(the company beats consensus this quarter).
Each metric is a fresh `claude -p` child on the CLI subscription (no API key).
"""

import json, subprocess, re, math, os, sys, argparse, pathlib
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from forecasting.pm_tracks import cfg

TODAY = date.today().isoformat()
_ap = argparse.ArgumentParser()
_ap.add_argument("--track", default="earnings", choices=["earnings", "kpis"])
_c = cfg(_ap.parse_args().track)
chosen = json.load(open(_c["chosen"]))
outp = _c["fcst"]

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


def run_claude(prompt):
    return subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-opus-4-8", "--max-turns", "40",
         "--dangerously-skip-permissions", "--output-format", "json"],
        capture_output=True, text=True, env=env, timeout=900,
    )


def ladder_prompt(c):
    return f"""You are ClaudeProphet, a live forecasting agent. Today is {TODAY}.
Forecast {c['co']}'s reported "{c['metric']}" for {c['period']} (a Polymarket market resolving around {c['close'][:10]}).
Use web search to find: the most recent company guidance, current analyst/consensus estimates, the prior-quarter and year-ago actual for THIS metric, and the recent growth trend. Then give a calibrated distribution for the figure that will actually be reported.
The market's current implied central estimate is about {c['market_median']:,.0f} — do NOT just copy it; form your own view.
Respond with STRICT JSON only. Write every number as a plain integer with NO commas or units:
{{"median": <number>, "p10": <number>, "p90": <number>, "reasoning": "<=3 sentences", "key_evidence": ["...","..."]}}"""


def binary_prompt(c):
    return f"""You are ClaudeProphet, a live forecasting agent. Today is {TODAY}.
A Polymarket market asks: "{c['question']}" (resolves around {c['close'][:10]}).
Estimate the probability that {c['co']} BEATS in {c['period']} as this market defines it.
Use web search to find: the consensus/analyst estimate this market is measured against, recent guidance, this company's recent history of beating vs missing, and any pre-announcement signals. Then give your calibrated probability.
The market currently prices this at about {c['market_p']:.2f} — do NOT just copy it; form your own view.
Respond with STRICT JSON only:
{{"p_yes": <number between 0 and 1>, "reasoning": "<=3 sentences", "key_evidence": ["...","..."]}}"""


def parse_json(res):
    d = json.loads(res.stdout)
    return d, json.loads(re.sub(r"(?<=\d),(?=\d)", "", extract(d.get("result", "")) or "{}"))


with outp.open("a") as _:
    pass

for c in chosen:
    try:
        if c["kind"] == "ladder":
            res = run_claude(ladder_prompt(c))
            d, f = parse_json(res)
            med, p10, p90 = float(f["median"]), float(f["p10"]), float(f["p90"])
            sigma = max(1e-9, (p90 - p10) / 2.5631)
            thr = [{"t": t, "cp_p": round(1 - ncdf((t - med) / sigma), 3)} for t in c["thresholds"]]
            rec = {**c, "cp_median": med, "cp_p10": p10, "cp_p90": p90, "cp_thresholds": thr,
                   "reasoning": f.get("reasoning", ""), "evidence": f.get("key_evidence", []),
                   "cost_usd": d.get("total_cost_usd")}
            summary = f"median={med}"
        else:  # binary
            res = run_claude(binary_prompt(c))
            d, f = parse_json(res)
            cp = float(f["p_yes"])
            cp = min(0.99, max(0.01, cp))
            rec = {**c, "cp_p": round(cp, 3),
                   "reasoning": f.get("reasoning", ""), "evidence": f.get("key_evidence", []),
                   "cost_usd": d.get("total_cost_usd")}
            summary = f"P(beat)={cp:.2f}"
    except Exception as e:
        rec = {**c, "error": str(e)[:200], "stdout_tail": (res.stdout[-300:] if 'res' in dir() else "")}
        summary = f"ERROR {str(e)[:60]}"
    with outp.open("a") as f2:
        f2.write(json.dumps(rec) + "\n")
    print(f"done [{c['kind']}]: {c['co']} — {c['metric'][:30]}  {summary}  cost={rec.get('cost_usd')}", flush=True)

print("ALL DONE", flush=True)
