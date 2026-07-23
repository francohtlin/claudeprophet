import json, subprocess, re, math, os, pathlib
from datetime import date

TODAY = date.today().isoformat()
BASE = pathlib.Path(__file__).resolve().parents[1] / "data" / "forecasts"
chosen = json.load(open(BASE/"_chosen.json"))
def ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))

env = dict(os.environ)
env.pop("ANTHROPIC_API_KEY", None); env.pop("ANTHROPIC_AUTH_TOKEN", None)

outp = BASE/"open_kpi_claudeprophet.jsonl"
with outp.open("a") as out:
    for c in chosen:
        prompt = f"""You are ClaudeProphet, a live forecasting agent. Today is {TODAY}.
Forecast {c['co']}'s reported "{c['metric']}" for {c['period']}, which will be reported around {c['resolves']}.
Use web search to find: the most recent company guidance, current analyst/consensus estimates, the prior-quarter and year-ago actual for THIS metric, and the recent growth trend. Then give a calibrated distribution for the figure that will actually be reported.
The market's current implied central estimate is about {c['market_median']:,.0f} — do NOT just copy it; form your own view and note where you differ.
Respond with STRICT JSON only. Write every number as a plain integer with NO commas, units, or thousands separators (e.g. 27500000, not 27,500,000):
{{"median": <number>, "p10": <number>, "p90": <number>, "reasoning": "<=3 sentences", "key_evidence": ["...","..."]}}"""
        res = subprocess.run(
            ["claude","-p",prompt,"--model","claude-opus-4-8","--max-turns","40",
             "--dangerously-skip-permissions","--output-format","json"],
            capture_output=True, text=True, env=env, timeout=900,
        )
        def extract(s):
            i = s.find("{")
            if i < 0: return None
            depth = 0
            for j in range(i, len(s)):
                if s[j] == "{": depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0: return s[i:j+1]
            return None
        def getnum(key, s):
            m = re.search(r'"'+key+r'"\s*:\s*\$?([\d,\.]+)', s)
            return float(m.group(1).replace(",","")) if m else None
        try:
            d = json.loads(res.stdout); r = d.get("result","")
            try:
                f = json.loads(re.sub(r"(?<=\d),(?=\d)", "", extract(r)))
                med, p10, p90 = float(f["median"]), float(f["p10"]), float(f["p90"])
                f_reason, f_ev = f.get("reasoning",""), f.get("key_evidence",[])
            except Exception:
                med, p10, p90 = getnum("median",r), getnum("p10",r), getnum("p90",r)
                if None in (med, p10, p90): raise ValueError("regex number extract failed")
                rm = re.search(r'"reasoning"\s*:\s*"(.*?)"\s*[,}\n]', r, re.S)
                f_reason, f_ev = (rm.group(1) if rm else ""), []
                f = {"reasoning": f_reason, "key_evidence": f_ev}
            sigma = max(1e-9, (p90-p10)/2.5631)
            thr = [{"t":t, "cp_p":round(1-ncdf((t-med)/sigma),3)} for t in c["thresholds"]]
            stu = (d.get("usage",{}) or {}).get("server_tool_use",{}) or {}
            rec = {**c, "cp_median":med, "cp_p10":p10, "cp_p90":p90,
                   "cp_thresholds":thr, "reasoning":f.get("reasoning",""),
                   "evidence":f.get("key_evidence",[]), "cost_usd":d.get("total_cost_usd"),
                   "web_searches":stu.get("web_search_requests"),
                   "web_fetches":stu.get("web_fetch_requests"),
                   "denials":len(d.get("permission_denials") or [])}
        except Exception as e:
            rec = {**c, "error":str(e)[:200], "stdout_tail":res.stdout[-400:], "stderr_tail":res.stderr[-300:]}
        with outp.open("a") as f2:
            f2.write(json.dumps(rec)+"\n")
        print(f"done: {c['co']} — {c['metric']}  median={rec.get('cp_median')}  cost={rec.get('cost_usd')}", flush=True)
print("ALL DONE", flush=True)
