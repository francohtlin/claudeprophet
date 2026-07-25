import json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import parse, fmtv, implied_median, group_markets, OPEN_MARKETS, FORECASTS

ROOT = Path(__file__).resolve().parents[1]
SRC = OPEN_MARKETS
FCST = FORECASTS
OUT = ROOT / "docs" / "index.html"
# Snapshot date = when prices were last pulled (file mtime), never hardcoded.
_snap_file = ROOT / "data" / "company_kpi_open.jsonl"
SNAP = (datetime.fromtimestamp(_snap_file.stat().st_mtime, timezone.utc)
        if _snap_file.exists() else datetime.now(timezone.utc)).strftime("%Y-%m-%d")

rows = [json.loads(l) for l in SRC.open()]

# forecasts keyed by (company_lower, period, resolves)
fdict = {}
if FCST.exists():
    for line in FCST.open():
        r = json.loads(line)
        if "cp_median" not in r: continue
        key = (r["co"].lower(), r["metric"].lower(), r["period"], r["resolves"])
        fdict[key] = {
            "cp_median": r["cp_median"], "cp_p10": r["cp_p10"], "cp_p90": r["cp_p90"],
            "cp_thr": {round(t["t"]): t["cp_p"] for t in r.get("cp_thresholds",[])},
            "reason": r.get("reasoning",""), "evidence": r.get("evidence",[]),
        }

# FutureSearch forecasts, keyed exactly like fdict so each metric row can show
# FutureSearch alongside ClaudeProphet and the market. Populated by
# `npm run futuresearch:forecast`; absent/empty until then (cells show "-").
FS_FCST = ROOT / "data" / "forecasts" / "open_kpi_futuresearch.jsonl"
fsdict = {}
if FS_FCST.exists():
    for line in FS_FCST.open():
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        thr = r.get("fs_thresholds") or []
        if not thr: continue
        key = (r["co"].lower(), r["metric"].lower(), r["period"], r["resolves"])
        fsdict[key] = {
            "fs_thr": {round(t["t"]): t["fs_p"] for t in thr},
            "fs_lad": [(float(t["t"]), t["fs_p"]) for t in thr],
            "fs_reason": r.get("fs_rationale") or "",
        }

groups = group_markets(rows)

def market_url(ticker: str):
    """Live Kalshi market page for a contract ticker: SERIES/EVENT (strike dropped).
    e.g. KXPM-26JULZYNSHIP-210000000 -> kalshi.com/markets/KXPM/KXPM-26JULZYNSHIP"""
    if not ticker or ticker.count("-") < 2:
        return None
    return f"https://kalshi.com/markets/{ticker.split('-')[0]}/{ticker.rsplit('-', 1)[0]}"

# The contract the paper portfolio bets on for each metric (max-divergence pick),
# keyed like fdict, so each displayed row can link to the exact live market.
bet_by_key = {}
_pf = ROOT / "data" / "portfolio.json"
if _pf.exists():
    for p in json.loads(_pf.read_text()).get("positions", []):
        url = market_url(p.get("ticker", ""))
        if not url:
            continue
        bet_by_key[(p["co"].lower(), p["metric"].lower(), p["period"], p["resolves"])] = {
            "url": url, "side": p["side"], "status": p["status"], "result": p.get("result"),
        }

data=[]; nf=0
for (co,metric,period,r), g in groups.items():
    mk = g["markets"]; vol=sum(m[2] for m in mk)
    ladder=[(m[0],m[1]) for m in mk if m[0] is not None]
    imp=implied_median(ladder); med=None; medop=""
    if imp: medop,medv=imp; med=fmtv(medv)
    fc = fdict.get((co.lower(), metric.lower(), period, r))
    fs = fsdict.get((co.lower(), metric.lower(), period, r))
    lad_disp=[]
    for v,p in sorted(ladder):
        e={"t":fmtv(v),"tv":v,"p":p}
        if fc and round(v) in fc["cp_thr"]: e["cp"]=fc["cp_thr"][round(v)]
        if fs and round(v) in fs["fs_thr"]: e["fs"]=fs["fs_thr"][round(v)]
        lad_disp.append(e)
    rec={"co":co,"metric":metric if period else (co+" KPI"),"period":period,"r":r,
         "n":len(mk),"v":vol,"med":med,"medop":medop,"lad":lad_disp,
         "cp":None,"edge":None,"reason":None,"cprange":None,
         "fs":None,"fs_op":None,"fs_reason":None,
         "bet":bet_by_key.get((co.lower(), metric.lower(), period, r))}
    if fc and imp:
        nf+=1
        rec["cp"]=fmtv(fc["cp_median"]); rec["cp_op"]="~"
        rec["cprange"]=fmtv(fc["cp_p10"])+" .. "+fmtv(fc["cp_p90"])
        rec["reason"]=fc["reason"]
        rec["edge"]=round((fc["cp_median"]-medv)/medv*100,1) if medv else None
    if fs:
        imp_fs=implied_median(fs["fs_lad"])
        if imp_fs:
            rec["fs_op"],fsv=imp_fs; rec["fs"]=fmtv(fsv)
        rec["fs_reason"]=fs["fs_reason"]
    data.append(rec)

total_metrics=len(data)
# Show only metrics we actually have a ClaudeProphet forecast for.
data=[d for d in data if d["cp"] is not None]
data.sort(key=lambda d:(d["r"], d["co"]))
companies=sorted({d["co"] for d in data})
cts=[d["r"] for d in data if d["r"]]
months=Counter(d["r"][:7] for d in data if d["r"])
live=sum(1 for d in data if d["medop"]=="~")
stats={"shown":len(data),"total":total_metrics,
       "contracts":sum(d["n"] for d in data),"companies":len(companies),"live":live,
       "positions":sum(1 for d in data if d.get("bet")),
       "next":min(cts) if cts else "-"}

# ---- paper portfolio: mark open positions to the latest pull ----
price_by_ticker={r["ticker"]:r.get("yes_mid") for r in rows}
PORT_PATH=ROOT/"data"/"portfolio.json"
portfolio={"positions":[],"summary":None,"pnl_curve":[]}
if PORT_PATH.exists():
    led=json.loads(PORT_PATH.read_text())
    pos_out=[]; unreal=0.0; realized=0.0; wins=0; losses=0; deployed=0.0
    for p in led["positions"]:
        cur_yes=price_by_ticker.get(p["ticker"])
        row={"co":p["co"],"metric":p["metric"],"period":p["period"],"r":p["resolves"],
             "q":p["question"],"side":p["side"],"entry":p["entry_price"],
             "cp_p":p["cp_p"],"mkt_entry":p["entry_yes_mid"],"stake":p["stake"],
             "url":market_url(p.get("ticker","")),
             "status":p["status"],"result":p.get("result"),"pnl":None,"cur":None}
        if p["status"]=="resolved":
            realized+=p["realized_pnl"] or 0.0
            row["pnl"]=p["realized_pnl"]
            if (p["realized_pnl"] or 0)>0: wins+=1
            else: losses+=1
        else:
            deployed+=p["stake"]
            if cur_yes is not None:
                cur = cur_yes if p["side"]=="YES" else round(1-cur_yes,3)
                row["cur"]=cur
                row["pnl"]=round(p["contracts"]*(cur-p["entry_price"]),2)
                unreal+=row["pnl"]
        pos_out.append(row)
    pos_out.sort(key=lambda x:(x["status"]!="resolved", -(abs(x["pnl"]) if x["pnl"] is not None else -1)))
    # cumulative realized-P&L equity curve: $0 start, one step per settled position
    resolved_sorted=sorted((p for p in led["positions"] if p["status"]=="resolved"),
                           key=lambda p:(p.get("resolved_date",""), -abs(p.get("realized_pnl") or 0)))
    cum=0.0
    pnl_curve=[{"date":led.get("created","")[:10],"co":"start","pnl":0.0,"cum":0.0}]
    for p in resolved_sorted:
        cum+=p.get("realized_pnl") or 0.0
        pnl_curve.append({"date":p.get("resolved_date",""),"co":p["co"],"metric":p["metric"],
                          "pnl":round(p.get("realized_pnl") or 0.0,2),"cum":round(cum,2)})
    portfolio={"positions":pos_out,"pnl_curve":pnl_curve,
               "summary":{"deployed":round(deployed,2),"unrealized":round(unreal,2),
                          "realized":round(realized,2),
                          "open":sum(1 for x in pos_out if x["status"]=="open"),
                          "wins":wins,"losses":losses,
                          "stake":led.get("stake_per_position",100),
                          "created":led.get("created","")[:10]}}

# ---- track record: scored resolved forecasts ----
SCORES=ROOT/"data"/"forecasts"/"resolved_scores.jsonl"
track=[json.loads(l) for l in SCORES.open()] if SCORES.exists() else []

# ===== Polymarket parallel track (separate files; never touches Kalshi data) =====
from collections import defaultdict as _dd
PM_OPEN=ROOT/"data"/"polymarket_kpi_open.jsonl"
PM_FCST=ROOT/"data"/"forecasts"/"open_pm_claudeprophet.jsonl"
PM_LEDG=ROOT/"data"/"pm_portfolio.json"

pm_rows=[json.loads(l) for l in PM_OPEN.open()] if PM_OPEN.exists() else []
pm_fc={}
if PM_FCST.exists():
    for l in PM_FCST.open():
        r=json.loads(l)
        if "cp_median" in r or "cp_p" in r:
            pm_fc[(r["co"],r["metric"],r["period"])]=r

pm_groups=_dd(list)
for r in pm_rows: pm_groups[(r["company"],r["metric"],r["period"])].append(r)

pm_data=[]
for (co,metric,period),rs in pm_groups.items():
    kind=rs[0]["outcome_kind"]; url=rs[0].get("market_url",""); close=(rs[0].get("close_time") or "")[:10]
    fc=pm_fc.get((co,metric,period))
    row={"co":co,"metric":metric,"period":period,"kind":kind,"close":close,"url":url,
         "market":"","our":"","edge":None,"edge_disp":"","reason":"","lad":[],"side":None}
    if kind=="binary":
        mid=rs[0].get("yes_mid")
        row["market"]=f"{mid:.0%}" if mid is not None else ""
        if fc and "cp_p" in fc:
            cp=fc["cp_p"]; row["our"]=f"{cp:.0%}"; row["reason"]=fc.get("reasoning","")
            if mid is not None:
                e=(cp-mid)*100; row["edge"]=round(e,1); row["edge_disp"]=f"{'+' if e>=0 else ''}{e:.0f} pts"
    else:
        ladder=[(r["threshold"],r["yes_mid"]) for r in rs if r.get("threshold") is not None and r.get("yes_mid") is not None]
        imp=implied_median(ladder)
        if imp: row["market"]=("≈ " if imp[0]=="~" else imp[0]+" ")+fmtv(imp[1])
        cp_thr={round(t["t"]):t["cp_p"] for t in (fc.get("cp_thresholds",[]) if fc else [])}
        row["lad"]=[{"t":fmtv(t),"p":p,"cp":cp_thr.get(round(t))} for t,p in sorted(ladder)]
        if fc and "cp_median" in fc:
            row["our"]="≈ "+fmtv(fc["cp_median"]); row["reason"]=fc.get("reasoning","")
            if imp and imp[0]=="~" and imp[1]:
                e=(fc["cp_median"]-imp[1])/imp[1]*100; row["edge"]=round(e,1); row["edge_disp"]=f"{'+' if e>=0 else ''}{e:.1f}%"
    pm_data.append(row)
pm_data.sort(key=lambda d:(d["our"]=="", d["close"] or "9999", d["co"]))

# PM paper portfolio, marked to the latest pull (mirrors the Kalshi portfolio shape)
pm_price={}
for r in pm_rows:
    k=(r["company"],r["metric"],r["period"],"BIN" if r["outcome_kind"]=="binary" else (round(r["threshold"]) if r.get("threshold") is not None else None))
    pm_price[k]=r.get("yes_mid")
pm_portfolio={"positions":[],"summary":None,"pnl_curve":[]}
if PM_LEDG.exists():
    pled=json.loads(PM_LEDG.read_text()); ppos=[]; punreal=preal=pdep=0.0; pwins=ploss=0
    for p in pled["positions"]:
        k=(p["co"],p["metric"],p["period"],"BIN" if p["kind"]=="binary" else (round(p["threshold"]) if p.get("threshold") is not None else None))
        cy=pm_price.get(k)
        prow={"co":p["co"],"metric":p["metric"],"period":p["period"],"r":(p.get("resolves") or (p.get("market_url") and "") or ""),
              "side":p["side"],"entry":p["entry_price"],"cp_p":p["cp_p"],"status":p["status"],
              "result":p.get("result"),"pnl":None,"cur":None,"url":p.get("market_url")}
        if p["status"]=="resolved":
            preal+=p["realized_pnl"] or 0.0; prow["pnl"]=p["realized_pnl"]
            if (p["realized_pnl"] or 0)>0: pwins+=1
            else: ploss+=1
        else:
            pdep+=p["stake"]
            if cy is not None:
                cur=cy if p["side"]=="YES" else round(1-cy,3)
                prow["cur"]=cur; prow["pnl"]=round(p["contracts"]*(cur-p["entry_price"]),2); punreal+=prow["pnl"]
        ppos.append(prow)
    ppos.sort(key=lambda x:(x["status"]!="resolved",-(abs(x["pnl"]) if x["pnl"] is not None else -1)))
    pres=sorted((p for p in pled["positions"] if p["status"]=="resolved"),
                key=lambda p:(p.get("resolved_date",""),-abs(p.get("realized_pnl") or 0)))
    pcum=0.0; pcurve=[{"date":pled.get("created","")[:10],"co":"start","pnl":0.0,"cum":0.0}]
    for p in pres:
        pcum+=p.get("realized_pnl") or 0.0
        pcurve.append({"date":p.get("resolved_date",""),"co":p["co"],"pnl":round(p.get("realized_pnl") or 0.0,2),"cum":round(pcum,2)})
    pm_portfolio={"positions":ppos,"pnl_curve":pcurve,
                  "summary":{"deployed":round(pdep,2),"unrealized":round(punreal,2),"realized":round(preal,2),
                             "open":sum(1 for x in ppos if x["status"]=="open"),"wins":pwins,"losses":ploss,
                             "stake":pled.get("stake_per_position",0),"created":pled.get("created","")[:10]}}

pm_stats={"shown":len(pm_data),"ladders":sum(1 for d in pm_data if d["kind"]!="binary"),
          "binaries":sum(1 for d in pm_data if d["kind"]=="binary"),
          "forecasted":sum(1 for d in pm_data if d["our"]),
          "positions":len(pm_portfolio["positions"]),
          "next":min((d["close"] for d in pm_data if d["close"]),default="-")}

DATA_JSON=json.dumps(data,separators=(",",":"))
MONTHS_JSON=json.dumps(sorted(months.items()))
STATS_JSON=json.dumps(stats)
PORT_JSON=json.dumps(portfolio,separators=(",",":"))
TRACK_JSON=json.dumps(track,separators=(",",":"))
PM_DATA_JSON=json.dumps(pm_data,separators=(",",":"))
PM_PORT_JSON=json.dumps(pm_portfolio,separators=(",",":"))
PM_STATS_JSON=json.dumps(pm_stats)

HTML = r"""<title>Company-KPI open markets</title>
<style>
:root{--bg:#f4f6f7;--surface:#ffffff;--surface-2:#fbfcfc;--border:#e3e7ea;--border-strong:#cfd5da;
--text:#161b1f;--muted:#5f6b73;--faint:#8a949b;--accent:#0d9488;--accent-weak:#d6f0ec;
--yes:#15803d;--no:#c2410c;--track:#eef1f2;--up:#0f766e;--down:#b45309;--up-bg:#d6f0ec;--down-bg:#fbebd2;
--font:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;}
@media (prefers-color-scheme:dark){:root{--bg:#0e1215;--surface:#161b1f;--surface-2:#12171a;--border:#262d33;--border-strong:#333c44;
--text:#e7ebee;--muted:#98a2a9;--faint:#6b757c;--accent:#2dd4bf;--accent-weak:#123a37;
--yes:#4ade80;--no:#fb923c;--track:#20272c;--up:#2dd4bf;--down:#f5b45a;--up-bg:#123a37;--down-bg:#3a2c14;}}
:root[data-theme="dark"]{--bg:#0e1215;--surface:#161b1f;--surface-2:#12171a;--border:#262d33;--border-strong:#333c44;
--text:#e7ebee;--muted:#98a2a9;--faint:#6b757c;--accent:#2dd4bf;--accent-weak:#123a37;
--yes:#4ade80;--no:#fb923c;--track:#20272c;--up:#2dd4bf;--down:#f5b45a;--up-bg:#123a37;--down-bg:#3a2c14;}
:root[data-theme="light"]{--bg:#f4f6f7;--surface:#ffffff;--surface-2:#fbfcfc;--border:#e3e7ea;--border-strong:#cfd5da;
--text:#161b1f;--muted:#5f6b73;--faint:#8a949b;--accent:#0d9488;--accent-weak:#d6f0ec;
--yes:#15803d;--no:#c2410c;--track:#eef1f2;--up:#0f766e;--down:#b45309;--up-bg:#d6f0ec;--down-bg:#fbebd2;}
/* FutureSearch accent (violet), theme-aware; data-theme wins over the media query. */
:root{--fs:#7c3aed}
@media (prefers-color-scheme:dark){:root{--fs:#a78bfa}}
:root[data-theme="dark"]{--fs:#a78bfa}
:root[data-theme="light"]{--fs:#7c3aed}
*{box-sizing:border-box}
.wrap{font-family:var(--font);color:var(--text);background:var(--bg);padding:22px;max-width:1180px;margin:0 auto;font-size:14px;line-height:1.5}
.tnum{font-variant-numeric:tabular-nums;font-family:var(--mono)}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:21px;font-weight:600;margin:0 0 3px}
.sub{color:var(--muted);font-size:13px}
.toggle{border:1px solid var(--border-strong);background:var(--surface);color:var(--muted);border-radius:8px;padding:7px 11px;cursor:pointer;font-size:13px}
.toggle:hover{border-color:var(--accent);color:var(--accent)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin-bottom:20px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:13px 15px}
.tile.hl{border-color:var(--accent)}
.tile .lab{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px}
.tile .val{font-size:24px;font-weight:600}
.tile .val.small{font-size:16px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:15px 16px;margin-bottom:18px}
.panel h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:0 0 12px}
.tl{display:flex;flex-direction:column;gap:6px}
.tlrow{display:grid;grid-template-columns:78px 1fr 44px;align-items:center;gap:10px}
.tlrow .mo{color:var(--muted);font-size:12px}.tlbar{height:14px;background:var(--accent);border-radius:3px;min-width:2px}.tlrow .n{text-align:right;color:var(--muted);font-size:12px}
.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
input[type=text],select{font-family:var(--font);font-size:13px;color:var(--text);background:var(--surface);border:1px solid var(--border-strong);border-radius:8px;padding:8px 10px;height:36px}
input[type=text]{min-width:200px;flex:1}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.chk{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:13px;cursor:pointer;user-select:none}
.count{color:var(--faint);font-size:12px;margin-left:auto}
.tblwrap{overflow-x:auto;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{position:sticky;top:0;background:var(--surface-2);text-align:left;padding:10px 12px;font-weight:500;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);cursor:pointer;white-space:nowrap}
thead th.num{text-align:right}thead th:hover{color:var(--accent)}
th .ar{opacity:.4;margin-left:3px}th.sorted .ar{opacity:1;color:var(--accent)}
tbody td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.grp{cursor:pointer}.grp:hover{background:var(--surface-2)}.grp.fc{background:var(--accent-weak)}
.co{font-weight:500;white-space:nowrap}
.metric{color:var(--text)}.metric .per{color:var(--faint);font-size:12px;margin-left:6px}
.rd{color:var(--muted)}.num{text-align:right}
.est{font-weight:500;color:var(--accent)}.nc{color:var(--faint);font-size:12px;margin-left:5px;font-weight:400}
.cp{font-weight:500}
.edge{font-weight:500;padding:2px 7px;border-radius:6px;font-size:12px}
.edge.up{color:var(--up);background:var(--up-bg)}.edge.down{color:var(--down);background:var(--down-bg)}
.dash{color:var(--faint)}
.chev{display:inline-block;width:12px;color:var(--faint);transition:transform .12s}.open .chev{transform:rotate(90deg);color:var(--accent)}
.detail td{background:var(--surface-2);padding:6px 12px 14px 34px}
.reason{color:var(--muted);font-size:12.5px;max-width:640px;margin:6px 0 12px;line-height:1.55}
.reason b{color:var(--text);font-weight:500}
.ladtitle{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin:2px 0 8px}
.lad{display:flex;flex-direction:column;gap:4px;max-width:560px}
.ladrow{display:grid;grid-template-columns:118px 1fr 38px 38px 38px;align-items:center;gap:9px;font-size:12px}
.ladrow.head{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em}
.ladrow .th{text-align:right;color:var(--muted)}
.ladtrack{height:8px;background:var(--track);border-radius:4px;overflow:hidden;position:relative}
.ladfill{height:100%;border-radius:4px;position:absolute;top:0;left:0}
.cptick{position:absolute;top:-2px;width:2px;height:12px;background:var(--accent)}
.fstick{position:absolute;top:-2px;width:2px;height:12px;background:var(--fs)}
.ladrow .pm{text-align:right;color:var(--muted)}.ladrow .pc{text-align:right;color:var(--accent);font-weight:500}
.ladrow .pf{text-align:right;color:var(--fs);font-weight:500}
.mktlink{color:var(--accent);text-decoration:none;font-weight:500;white-space:nowrap}
.mktlink:hover{text-decoration:underline}
.metric .mktlink,.portml{font-size:12px;margin-left:6px}
.betline{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--muted);margin:2px 0 12px;flex-wrap:wrap}
.betline .bs{font-weight:600;padding:1px 7px;border-radius:6px;font-size:11px}
.betline .bs.YES{color:var(--yes);background:var(--up-bg)}
.betline .bs.NO{color:var(--no);background:var(--down-bg)}
.foot{color:var(--faint);font-size:12px;margin-top:14px;line-height:1.6}.foot b{color:var(--muted);font-weight:500}

/* ---- tab bar (buttons are generated from the .tabpanel sections; see script) ---- */
.tabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--border);margin:0 0 20px}
.tab{appearance:none;-webkit-appearance:none;font-family:var(--font);font-size:13px;font-weight:500;line-height:1.3;
color:var(--muted);background:transparent;border:1px solid transparent;border-radius:9px 9px 0 0;
padding:9px 14px;margin-bottom:-1px;cursor:pointer}
.tab:hover{color:var(--accent);background:var(--surface-2)}
.tab[aria-selected="true"]{color:var(--accent);background:var(--surface);border-color:var(--border);
border-bottom-color:var(--surface);box-shadow:inset 0 2px 0 0 var(--accent)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tabpanel[hidden]{display:none!important}
.tabpanel:focus{outline:none}

/* ---- explainer tab ---- */
.doc{max-width:860px}
.doc h3{font-size:14.5px;font-weight:600;color:var(--text);margin:24px 0 9px;letter-spacing:0}
.doc h3:first-child{margin-top:0}
.doc p{margin:0 0 11px}
.doc p.lead{color:var(--muted);font-size:13.5px}
.doc ol{margin:0 0 4px;padding:0;list-style:none;counter-reset:step}
.doc ol li{counter-increment:step;position:relative;padding:0 0 15px 34px}
.doc ol li::before{content:counter(step);position:absolute;left:0;top:0;width:22px;height:22px;
border-radius:50%;background:var(--accent-weak);color:var(--accent);font-family:var(--mono);
font-size:11.5px;font-weight:600;display:flex;align-items:center;justify-content:center}
.doc ol li .d{display:block;color:var(--muted);margin-top:3px}
.doc code{font-family:var(--mono);font-size:12px;background:var(--surface-2);border:1px solid var(--border);
border-radius:5px;padding:1px 5px;color:var(--text);overflow-wrap:anywhere}
.formula{font-family:var(--mono);font-size:13px;line-height:1.9;background:var(--surface-2);
border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:9px;
padding:11px 14px;margin:0 0 13px;color:var(--text);overflow-x:auto}
.fig{display:flex;justify-content:center;margin:16px 0 6px}
.fig svg{display:block;width:100%;height:auto;max-width:420px}
.fig.wide svg{max-width:540px}
.cap{color:var(--faint);font-size:12px;text-align:center;margin:0 0 6px;line-height:1.5}
.doc ul.plain{margin:0 0 12px;padding-left:19px;color:var(--muted)}
.doc ul.plain li{margin-bottom:6px}
.doc ul.plain b{color:var(--text);font-weight:500}
</style>

<div class="wrap">
  <div class="top">
    <div><h1>Company-KPI open markets</h1>
      <div class="sub">Company reports &amp; KPIs &middot; Kalshi + Polymarket in parallel &middot; live research &middot; prices pulled __SNAP__</div></div>
    <button class="toggle" id="tg" aria-label="Toggle theme">Theme</button>
  </div>

  <!-- Tab bar. The buttons are generated at runtime from the <section class="tabpanel">
       elements below, so adding a tab = adding ONE section with data-tab + data-tab-label. -->
  <div class="tabs" id="tabs" role="tablist" aria-label="Sections"></div>

  <section class="tabpanel" data-tab="dashboard" data-tab-label="Kalshi" role="tabpanel" tabindex="0">
  <div class="tiles" id="tiles"></div>

  <div class="panel" id="trackpanel" style="display:none">
    <h2>Track record &mdash; resolved forecasts</h2>
    <div class="tblwrap" style="border-radius:10px"><table>
      <thead><tr>
        <th>Metric</th><th class="num">ClaudeProphet</th><th class="num">Market</th>
        <th class="num">Actual</th><th class="num">CP Brier</th><th class="num">Mkt Brier</th><th class="num">FS Brier</th><th class="num">Winner</th>
      </tr></thead>
      <tbody id="trackbody"></tbody>
    </table></div>
    <div class="foot" style="margin-top:10px">
      Brier scores (lower is better) computed per threshold contract against the settled
      outcome, market prices taken at the same pre-release snapshot as the forecast.
    </div>
  </div>

  <div class="panel" id="portpanel" style="display:none">
    <h2>Paper portfolio &mdash; tracking, not trading</h2>
    <div class="tiles" id="porttiles" style="margin-bottom:14px"></div>
    <div id="pnlwrap" style="display:none;margin-bottom:16px">
      <div class="lab" style="margin-bottom:6px">Cumulative realized P&amp;L</div>
      <div id="pnlchart"></div>
    </div>
    <div class="tblwrap" style="border-radius:10px"><table>
      <thead><tr>
        <th>Side</th><th>Position</th><th class="num">Resolves</th>
        <th class="num">Entry</th><th class="num">Our P</th><th class="num">Now</th>
        <th class="num">P&amp;L</th>
      </tr></thead>
      <tbody id="portbody"></tbody>
    </table></div>
    <div class="foot" style="margin-top:10px">
      $1,000 paper bankroll split equally: one position per forecasted metric, on
      the contract where ClaudeProphet (live research) most disagrees with the
      market (min 5 pt gap), entered at the mid. P&amp;L is marked to the latest
      price pull and realizes when markets settle. Paper only &mdash; nothing is traded.
    </div>
  </div>

  <div class="panel"><h2>Resolutions by month (metrics)</h2><div class="tl" id="tl"></div></div>
  <div class="filters">
    <input type="text" id="q" placeholder="Search company or metric..." aria-label="Search">
    <select id="mo" aria-label="Filter by month"></select>
    <label class="chk"><input type="checkbox" id="lv"> Uncertain only</label>
    <span class="count" id="cnt"></span>
  </div>
  <div class="tblwrap"><table id="ftable">
    <thead><tr>
      <th data-k="co">Company <span class="ar">&#8597;</span></th>
      <th data-k="metric">Metric <span class="ar">&#8597;</span></th>
      <th data-k="r" class="sorted">Resolves <span class="ar">&#8593;</span></th>
      <th data-k="med" class="num">Market est. <span class="ar">&#8597;</span></th>
      <th data-k="cp" class="num">ClaudeProphet <span class="ar">&#8597;</span></th>
      <th data-k="fs" class="num">FutureSearch <span class="ar">&#8597;</span></th>
      <th data-k="edge" class="num">Edge <span class="ar">&#8597;</span></th>
    </tr></thead>
    <tbody id="tb"></tbody>
  </table></div>
  <div class="foot">
    <b>Market est.</b> = market-implied central value (50% threshold crossing). <b>ClaudeProphet</b> = our live-researched median forecast of the reported figure. <b>FutureSearch</b> = the same central value implied by FutureSearch's independent forecast (from <code>npm run futuresearch:forecast</code>; &ldquo;&mdash;&rdquo; until run). <b>Edge</b> = ClaudeProphet vs market, % of the metric. Click a forecasted row for the reasoning and a threshold-by-threshold market-vs-ClaudeProphet-vs-FutureSearch comparison.
  </div>
  </section>

  <section class="tabpanel" data-tab="polymarket" data-tab-label="Polymarket" role="tabpanel" tabindex="0" hidden>
  <div class="tiles" id="pm_tiles"></div>
  <div class="panel" id="pm_portpanel" style="display:none">
    <h2>Polymarket paper portfolio &mdash; tracking, not trading</h2>
    <div class="tiles" id="pm_porttiles" style="margin-bottom:14px"></div>
    <div id="pm_pnlwrap" style="display:none;margin-bottom:16px">
      <div class="lab" style="margin-bottom:6px">Cumulative realized P&amp;L</div>
      <div id="pm_pnlchart"></div>
    </div>
    <div class="tblwrap"><table>
      <thead><tr>
        <th>Side</th><th>Position</th><th class="num">Resolves</th>
        <th class="num">Entry</th><th class="num">Our P</th><th class="num">Now</th>
        <th class="num">P&amp;L</th>
      </tr></thead>
      <tbody id="pm_portbody"></tbody>
    </table></div>
    <div class="foot" style="margin-top:10px">
      Same $1,000 paper book and max-divergence rule as the Kalshi track, run in
      parallel on Polymarket &mdash; here on company earnings: we bet the beat/miss
      contract where our P(beat) most disagrees with the market (min 5 pt gap).
      Marked to the latest Gamma pull; settles via UMA resolution. Paper only
      &mdash; nothing is traded.
    </div>
  </div>
  <div class="panel">
    <h2>Polymarket forecasts &mdash; company earnings (beat / miss)</h2>
    <div class="tblwrap"><table id="pm_ftable">
      <thead><tr>
        <th>Company</th><th>Market</th><th class="num">Resolves</th>
        <th class="num">Mkt P(beat)</th><th class="num">Our P(beat)</th><th class="num">Edge</th>
      </tr></thead>
      <tbody id="pm_tb"></tbody>
    </table></div>
    <div class="foot">
      Polymarket (Gamma API) &ldquo;will &lt;company&gt; beat quarterly earnings?&rdquo; markets.
      <b>Mkt P(beat)</b> = market price; <b>Our P(beat)</b> = our live-researched probability;
      <b>Edge</b> = our view vs the market (percentage points). We bet the side we most disagree
      with the market on. Click a forecasted row for the reasoning. Links open the live Polymarket market.
    </div>
  </div>
  </section>

  <section class="tabpanel" data-tab="how" data-tab-label="How forecasting works" role="tabpanel" tabindex="0" hidden>
  <div class="panel doc">
    <h2>How forecasting works</h2>
    <p class="lead">Kalshi lists a company KPI not as one contract but as a <b>ladder of threshold
      contracts</b> &mdash; &ldquo;will this company report revenue above $40 billion in Q3 2026?&rdquo;,
      repeated at a dozen different thresholds. ClaudeProphet collapses each ladder back into the single
      number it is really about, has Claude research that number, and then converts one probability
      distribution into a price for every rung of the ladder. Those per-threshold probabilities are what
      this dashboard puts side by side with the market.</p>

    <div class="fig"><svg viewBox="0 0 400 646" role="img"
      aria-label="Pipeline: pull Kalshi markets, group into ladders, keep uncertain metrics, Claude researches, median and p10/p90, normal distribution, per-threshold probabilities, compare vs market">
      <defs>
        <marker id="cpArrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--border-strong)"/>
        </marker>
      </defs>
      <g font-family="var(--font)">
        <!-- 1 -->
        <rect x="26" y="10" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="52" cy="37" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="41" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">1</text>
        <text x="74" y="33" font-size="13" font-weight="600" fill="var(--text)">Pull open KPI markets from Kalshi</text>
        <text x="74" y="50" font-family="var(--mono)" font-size="11" fill="var(--muted)">pull_kpi_markets.py &rarr; company_kpi_open.jsonl</text>
        <line x1="200" y1="64" x2="200" y2="88" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 2 -->
        <rect x="26" y="90" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="52" cy="117" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="121" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">2</text>
        <text x="74" y="113" font-size="13" font-weight="600" fill="var(--text)">Group contracts into metric ladders</text>
        <text x="74" y="130" font-family="var(--mono)" font-size="11" fill="var(--muted)">company, metric, period, resolve date</text>
        <line x1="200" y1="144" x2="200" y2="168" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 3 -->
        <rect x="26" y="170" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="52" cy="197" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="201" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">3</text>
        <text x="74" y="193" font-size="13" font-weight="600" fill="var(--text)">Keep only the uncertain metrics</text>
        <text x="74" y="210" font-family="var(--mono)" font-size="11" fill="var(--muted)">select_kpi.py &rarr; _chosen.json (top N)</text>
        <line x1="200" y1="224" x2="200" y2="248" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 4 -->
        <rect x="26" y="250" width="348" height="54" rx="10" fill="var(--accent-weak)" stroke="var(--accent)" stroke-width="1.5"/>
        <circle cx="52" cy="277" r="12" fill="var(--accent)"/>
        <text x="52" y="281" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--surface)">4</text>
        <text x="74" y="273" font-size="13" font-weight="600" fill="var(--text)">Claude Opus researches the number</text>
        <text x="74" y="290" font-family="var(--mono)" font-size="11" fill="var(--muted)">forecast_kpi.py &middot; claude -p &middot; web search</text>
        <line x1="200" y1="304" x2="200" y2="328" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 5 -->
        <rect x="26" y="330" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="52" cy="357" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="361" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">5</text>
        <text x="74" y="353" font-size="13" font-weight="600" fill="var(--text)">It returns median, p10 and p90</text>
        <text x="74" y="370" font-family="var(--mono)" font-size="11" fill="var(--muted)">strict JSON + reasoning + key_evidence</text>
        <line x1="200" y1="384" x2="200" y2="408" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 6 -->
        <rect x="26" y="410" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="52" cy="437" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="441" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">6</text>
        <text x="74" y="433" font-size="13" font-weight="600" fill="var(--text)">Fit a normal distribution</text>
        <text x="74" y="450" font-family="var(--mono)" font-size="11" fill="var(--muted)">&sigma; = (p90 &minus; p10) / 2.5631</text>
        <line x1="200" y1="464" x2="200" y2="488" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 7 -->
        <rect x="26" y="490" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="52" cy="517" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="521" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">7</text>
        <text x="74" y="513" font-size="13" font-weight="600" fill="var(--text)">Price every rung of the ladder</text>
        <text x="74" y="530" font-family="var(--mono)" font-size="11" fill="var(--muted)">P(&ge; t) = 1 &minus; &Phi;((t &minus; median) / &sigma;)</text>
        <line x1="200" y1="544" x2="200" y2="568" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#cpArrow)"/>
        <!-- 8 -->
        <rect x="26" y="570" width="348" height="54" rx="10" fill="var(--surface-2)" stroke="var(--accent)" stroke-width="1.5"/>
        <circle cx="52" cy="597" r="12" fill="var(--accent-weak)"/>
        <text x="52" y="601" text-anchor="middle" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--accent)">8</text>
        <text x="74" y="593" font-size="13" font-weight="600" fill="var(--text)">Compare against the market price</text>
        <text x="74" y="610" font-family="var(--mono)" font-size="11" fill="var(--muted)">open_kpi_claudeprophet.jsonl &rarr; this page</text>
      </g>
    </svg></div>
    <div class="cap">Every step is a plain Python script in <code>forecasting/</code>; only step 4 calls a model.</div>

    <h3>Step by step</h3>
    <ol>
      <li><b>Pull the open markets.</b>
        <span class="d"><code>forecasting/pull_kpi_markets.py</code> walks every KPI-tagged Kalshi series in
        the Companies and Financials categories and records each open contract &mdash; question, close
        time, current mid price, volume &mdash; into <code>data/company_kpi_open.jsonl</code>.</span></li>
      <li><b>Group contracts into metrics.</b>
        <span class="d"><code>forecasting/select_kpi.py</code> parses each question into a
        (threshold, metric, period) triple and groups the contracts by
        <i>(company, metric, period, resolve date)</i>. One group = one threshold ladder = exactly one
        number that has to be forecast.</span></li>
      <li><b>Keep only what is genuinely uncertain.</b>
        <span class="d">The market-implied median is the threshold at which the market&rsquo;s P(Yes)
        crosses 50%, interpolated between the two rungs that straddle it. If that crossing falls
        <i>inside</i> the ladder the metric is uncertain and worth forecasting; if the entire ladder is
        already priced above or below, the market has effectively settled it and it is dropped. Metrics
        that were already forecast are skipped, the survivors are ranked by traded volume, and the top N
        are written to <code>data/forecasts/_chosen.json</code>.</span></li>
      <li><b>Claude researches each chosen metric.</b>
        <span class="d"><code>forecasting/forecast_kpi.py</code> spawns one separate <code>claude -p</code>
        child process per metric (model <code>claude-opus-4-8</code>, up to 40 turns, web search enabled).
        The prompt asks it to find the most recent company guidance, the current analyst/consensus
        estimate, the prior-quarter and year-ago actuals for that same metric, and the recent growth
        trend. It is told the market&rsquo;s implied central estimate and explicitly instructed <i>not</i>
        to copy it. The child must reply with strict JSON:
        <code>{median, p10, p90, reasoning, key_evidence}</code>.</span></li>
      <li><b>Turn three numbers into a distribution.</b>
        <span class="d">The p10/p90 pair is read as the 80% central interval of a normal distribution, so
        the standard deviation is recovered as <code>sigma = (p90 - p10) / 2.5631</code>. The forecast is
        then the normal distribution centred on the median with that sigma.</span></li>
      <li><b>Price every rung of the ladder.</b>
        <span class="d">For each threshold <i>t</i> in that metric&rsquo;s ladder the script evaluates
        <code>P(value &ge; t) = 1 - &Phi;((t - median) / sigma)</code> and stores the result in
        <code>cp_thresholds</code>. A single researched view of the number therefore produces a full,
        internally consistent set of contract probabilities.</span></li>
      <li><b>Append and compare.</b>
        <span class="d">Each record &mdash; forecast, thresholds, reasoning, evidence, cost and web-search
        counts &mdash; is appended to <code>data/forecasts/open_kpi_claudeprophet.jsonl</code>. The
        dashboard joins that back onto the live market snapshot and shows, contract by contract,
        ClaudeProphet&rsquo;s probability next to the market&rsquo;s price for the same threshold.</span></li>
    </ol>

    <h3>The core of it: one distribution, many contracts</h3>
    <p>The whole method rests on a single conversion. Claude gives three numbers; those three numbers
      define a bell curve; the area of that curve to the right of a threshold is the probability that
      the contract settles Yes.</p>
    <div class="formula">sigma = (p90 &minus; p10) / 2.5631<br>P(value &ge; t) = 1 &minus; &Phi;( (t &minus; median) / sigma )</div>
    <p>The constant 2.5631 is the width of the 10th-to-90th percentile band of a standard normal
      distribution, measured in standard deviations (2 &times; 1.2816). Dividing the forecast&rsquo;s
      p10&ndash;p90 spread by it recovers sigma, and &Phi; is the standard normal CDF. A wider
      p10&ndash;p90 means a less confident forecast, which flattens every probability on the ladder
      toward 50%.</p>

    <div class="fig wide"><svg viewBox="0 0 420 262" role="img"
      aria-label="Bell curve with p10, median and p90 marked; the shaded area to the right of a threshold t is the probability the contract settles Yes">
      <g font-family="var(--font)">
        <path d="M 245.0 200.0 L 245.0 98.3 L 248.9 104.7 L 252.8 111.3 L 256.6 117.8 L 260.5 124.4 L 264.4 130.8 L 268.2 137.1 L 272.1 143.1 L 276.0 148.9 L 279.9 154.4 L 283.8 159.5 L 287.6 164.3 L 291.5 168.7 L 295.4 172.7 L 299.2 176.3 L 303.1 179.6 L 307.0 182.5 L 310.9 185.1 L 314.8 187.4 L 318.6 189.4 L 322.5 191.2 L 326.4 192.6 L 330.2 193.9 L 334.1 195.0 L 338.0 195.9 L 341.9 196.7 L 345.8 197.3 L 349.6 197.9 L 353.5 198.3 L 357.4 198.7 L 361.2 198.9 L 365.1 199.2 L 369.0 199.4 L 372.9 199.5 L 376.8 199.6 L 380.6 199.7 L 384.5 199.8 L 388.4 199.8 L 392.2 199.9 L 396.1 199.9 L 400.0 199.9 L 400.0 200.0 Z"
          fill="var(--accent)" fill-opacity="0.3"/>
        <path d="M 30.0 199.7 L 41.1 199.4 L 52.2 198.7 L 63.3 197.5 L 74.4 195.4 L 85.5 192.0 L 96.6 186.6 L 104.0 181.8 L 111.4 175.7 L 118.8 168.3 L 126.2 159.6 L 133.6 149.5 L 141.0 138.3 L 148.4 126.2 L 155.8 113.7 L 163.2 101.3 L 170.6 89.5 L 178.0 79.0 L 185.4 70.4 L 192.8 64.1 L 200.2 60.6 L 203.9 60.0 L 207.6 60.2 L 215.0 62.8 L 222.4 68.2 L 229.8 76.2 L 237.2 86.2 L 244.6 97.7 L 252.0 110.0 L 259.4 122.5 L 266.8 134.8 L 274.2 146.3 L 281.6 156.7 L 289.0 165.9 L 296.4 173.7 L 303.8 180.1 L 311.2 185.3 L 318.6 189.4 L 326.0 192.5 L 333.4 194.8 L 340.8 196.5 L 348.2 197.7 L 355.6 198.5 L 363.0 199.0 L 370.4 199.4 L 377.8 199.6 L 385.2 199.8 L 392.6 199.9 L 400.0 199.9"
          fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
        <line x1="24" y1="200" x2="404" y2="200" stroke="var(--border-strong)" stroke-width="1"/>
        <line x1="205" y1="60" x2="205" y2="200" stroke="var(--faint)" stroke-width="1" stroke-dasharray="3 4"/>
        <line x1="140.9" y1="138.4" x2="140.9" y2="200" stroke="var(--faint)" stroke-width="1" stroke-dasharray="3 4"/>
        <line x1="269.1" y1="138.4" x2="269.1" y2="200" stroke="var(--faint)" stroke-width="1" stroke-dasharray="3 4"/>
        <line x1="245" y1="92" x2="245" y2="207" stroke="var(--text)" stroke-width="1.6"/>
        <text x="249" y="84" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--text)">threshold t</text>
        <text x="140.9" y="216" text-anchor="middle" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">p10</text>
        <text x="205" y="216" text-anchor="middle" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">median</text>
        <text x="269.1" y="216" text-anchor="middle" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">p90</text>
        <line x1="140.9" y1="232" x2="269.1" y2="232" stroke="var(--faint)" stroke-width="1"/>
        <line x1="140.9" y1="228" x2="140.9" y2="236" stroke="var(--faint)" stroke-width="1"/>
        <line x1="269.1" y1="228" x2="269.1" y2="236" stroke="var(--faint)" stroke-width="1"/>
        <text x="205" y="250" text-anchor="middle" font-size="11.5" fill="var(--faint)">80% of the mass = 2.5631 &sigma;</text>
        <text x="330" y="120" text-anchor="middle" font-size="12.5" font-weight="600" fill="var(--accent)">P(value &ge; t)</text>
        <line x1="330" y1="128" x2="308" y2="176" stroke="var(--accent)" stroke-width="1" stroke-dasharray="2 3"/>
        <text x="30" y="34" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">&sigma; = (p90 &minus; p10) / 2.5631</text>
      </g>
    </svg></div>
    <div class="cap">The shaded tail is exactly the probability quoted for that threshold contract.
      Every rung of the ladder is a different vertical line on the same curve.</div>

    <h3>Reading the Dashboard tab</h3>
    <ul class="plain">
      <li><b>Market est.</b> &mdash; the market-implied central value: where the ladder&rsquo;s P(Yes)
        crosses 50%.</li>
      <li><b>ClaudeProphet</b> &mdash; the researched median, with the p10&ndash;p90 range shown when a
        row is expanded.</li>
      <li><b>Edge</b> &mdash; how far the ClaudeProphet median sits from the market-implied one, as a
        percentage of the metric. A large edge means the two disagree about the underlying number, not
        merely about one contract.</li>
      <li><b>Expanded rows</b> &mdash; the reasoning Claude returned plus the full ladder: market price
        as the bar, the ClaudeProphet probability as the tick mark on the same bar.</li>
      <li><b>Track record</b> and <b>Paper portfolio</b> &mdash; Brier scores against settled outcomes,
        and a $1,000 paper bankroll that takes the single largest disagreement per metric. Paper only;
        nothing is traded.</li>
    </ul>

    <h3>Caveats worth knowing</h3>
    <ul class="plain">
      <li>The distribution is assumed normal and symmetric. Real KPI outcomes are often skewed, so the
        far tails of the ladder are the least trustworthy part of the output.</li>
      <li>The p10/p90 come from a language model&rsquo;s self-assessed uncertainty, which is the main
        thing the track record is there to test.</li>
      <li>Market prices are mid-quotes from the latest snapshot, not executable prices, and they move
        after the forecast is made.</li>
    </ul>
  </div>
  </section>

  <section class="tabpanel" data-tab="strategy" data-tab-label="Trading strategy" role="tabpanel" tabindex="0" hidden>
  <div class="panel doc">
    <h2>Trading strategy</h2>
    <p class="lead">Once a metric has been forecast, ClaudeProphet has a probability for
      <i>every</i> rung of that metric&rsquo;s threshold ladder, and the market has a price for every
      rung too. The strategy is deliberately narrow: for each forecasted metric it takes
      <b>one</b> position, on the <b>single contract where the two disagree most</b>. Everything
      lives in <code>forecasting/portfolio.py</code>, the ledger is
      <code>data/portfolio.json</code>, and it is <b>paper only</b> &mdash; nothing is ever traded,
      no broker is contacted, no order is placed.</p>

    <div class="fig wide"><svg viewBox="0 0 560 340" role="img"
      aria-label="A threshold ladder: for each rung the market price is a bar and the ClaudeProphet probability is a tick on the same track. The rung with the largest gap between them, 0.34 market versus 0.52 ClaudeProphet, is highlighted as the contract that is bet.">
      <g font-family="var(--font)">
        <!-- column headers -->
        <text x="96" y="36" text-anchor="end" font-size="11" fill="var(--faint)">threshold</text>
        <text x="244" y="36" text-anchor="middle" font-size="11" fill="var(--faint)">market price (bar) vs ClaudeProphet P (tick)</text>
        <text x="424" y="36" text-anchor="end" font-size="11" fill="var(--faint)">mkt</text>
        <text x="464" y="36" text-anchor="end" font-size="11" fill="var(--faint)">CP</text>
        <text x="544" y="36" text-anchor="end" font-size="11" fill="var(--faint)">gap</text>

        <!-- highlighted winner row (drawn first so it sits behind) -->
        <rect x="6" y="145" width="548" height="30" rx="8" fill="var(--accent-weak)" stroke="var(--accent)" stroke-width="1.5"/>

        <!-- row 1: >= 38B, mkt .94, cp .97 -->
        <text x="96" y="62" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">&ge; $38B</text>
        <rect x="367.2" y="47" width="8.4" height="22" fill="var(--accent)" fill-opacity="0.14"/>
        <rect x="104" y="52" width="280" height="12" rx="6" fill="var(--track)" stroke="var(--border)"/>
        <rect x="104" y="52" width="263.2" height="12" rx="6" fill="var(--yes)"/>
        <line x1="375.6" y1="47" x2="375.6" y2="69" stroke="var(--accent)" stroke-width="2.5"/>
        <text x="424" y="62" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.94</text>
        <text x="464" y="62" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--accent)">0.97</text>
        <text x="544" y="62" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.03</text>

        <!-- row 2: >= 40B, mkt .81, cp .88 -->
        <text x="96" y="96" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">&ge; $40B</text>
        <rect x="330.8" y="81" width="19.6" height="22" fill="var(--accent)" fill-opacity="0.14"/>
        <rect x="104" y="86" width="280" height="12" rx="6" fill="var(--track)" stroke="var(--border)"/>
        <rect x="104" y="86" width="226.8" height="12" rx="6" fill="var(--yes)"/>
        <line x1="350.4" y1="81" x2="350.4" y2="103" stroke="var(--accent)" stroke-width="2.5"/>
        <text x="424" y="96" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.81</text>
        <text x="464" y="96" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--accent)">0.88</text>
        <text x="544" y="96" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.07</text>

        <!-- row 3: >= 42B, mkt .58, cp .71 -->
        <text x="96" y="130" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">&ge; $42B</text>
        <rect x="266.4" y="115" width="36.4" height="22" fill="var(--accent)" fill-opacity="0.14"/>
        <rect x="104" y="120" width="280" height="12" rx="6" fill="var(--track)" stroke="var(--border)"/>
        <rect x="104" y="120" width="162.4" height="12" rx="6" fill="var(--yes)"/>
        <line x1="302.8" y1="115" x2="302.8" y2="137" stroke="var(--accent)" stroke-width="2.5"/>
        <text x="424" y="130" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.58</text>
        <text x="464" y="130" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--accent)">0.71</text>
        <text x="544" y="130" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.13</text>

        <!-- row 4: >= 44B, mkt .34, cp .52  <-- max divergence -->
        <text x="96" y="164" text-anchor="end" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--text)">&ge; $44B</text>
        <rect x="199.2" y="149" width="50.4" height="22" fill="var(--accent)" fill-opacity="0.32"/>
        <rect x="104" y="154" width="280" height="12" rx="6" fill="var(--track)" stroke="var(--border)"/>
        <rect x="104" y="154" width="95.2" height="12" rx="6" fill="var(--no)"/>
        <line x1="249.6" y1="147" x2="249.6" y2="173" stroke="var(--accent)" stroke-width="3"/>
        <text x="424" y="164" text-anchor="end" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--text)">0.34</text>
        <text x="464" y="164" text-anchor="end" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--accent)">0.52</text>
        <text x="544" y="164" text-anchor="end" font-family="var(--mono)" font-size="11.5" font-weight="700" fill="var(--accent)">0.18</text>

        <!-- row 5: >= 46B, mkt .17, cp .27 -->
        <text x="96" y="198" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">&ge; $46B</text>
        <rect x="151.6" y="183" width="28" height="22" fill="var(--accent)" fill-opacity="0.14"/>
        <rect x="104" y="188" width="280" height="12" rx="6" fill="var(--track)" stroke="var(--border)"/>
        <rect x="104" y="188" width="47.6" height="12" rx="6" fill="var(--no)"/>
        <line x1="179.6" y1="183" x2="179.6" y2="205" stroke="var(--accent)" stroke-width="2.5"/>
        <text x="424" y="198" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.17</text>
        <text x="464" y="198" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--accent)">0.27</text>
        <text x="544" y="198" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--muted)">0.10</text>

        <!-- row 6: >= 48B, mkt .02 -> outside the price band, skipped -->
        <text x="96" y="232" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--faint)">&ge; $48B</text>
        <rect x="104" y="222" width="280" height="12" rx="6" fill="var(--track)" stroke="var(--border)"/>
        <rect x="104" y="222" width="5.6" height="12" rx="2.8" fill="var(--faint)"/>
        <line x1="129.2" y1="217" x2="129.2" y2="239" stroke="var(--faint)" stroke-width="2.5"/>
        <text x="424" y="232" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--faint)">0.02</text>
        <text x="464" y="232" text-anchor="end" font-family="var(--mono)" font-size="11.5" fill="var(--faint)">0.09</text>
        <text x="544" y="232" text-anchor="end" font-size="11" fill="var(--faint)">skipped</text>

        <!-- probability axis -->
        <line x1="104" y1="248" x2="384" y2="248" stroke="var(--border-strong)" stroke-width="1"/>
        <line x1="104" y1="248" x2="104" y2="253" stroke="var(--border-strong)" stroke-width="1"/>
        <line x1="244" y1="248" x2="244" y2="253" stroke="var(--border-strong)" stroke-width="1"/>
        <line x1="384" y1="248" x2="384" y2="253" stroke="var(--border-strong)" stroke-width="1"/>
        <text x="104" y="266" text-anchor="middle" font-family="var(--mono)" font-size="11" fill="var(--faint)">0.00</text>
        <text x="244" y="266" text-anchor="middle" font-family="var(--mono)" font-size="11" fill="var(--faint)">0.50</text>
        <text x="384" y="266" text-anchor="middle" font-family="var(--mono)" font-size="11" fill="var(--faint)">1.00</text>

        <!-- verdict -->
        <rect x="6" y="278" width="548" height="58" rx="9" fill="var(--surface-2)" stroke="var(--accent)"/>
        <text x="20" y="298" font-size="12.5" font-weight="600" fill="var(--accent)">Biggest disagreement wins: 0.52 vs 0.34 &rarr; edge 0.18, and 0.18 &ge; MIN_EDGE 0.05</text>
        <text x="20" y="316" font-family="var(--mono)" font-size="11.5" fill="var(--text)">side = YES (cp &gt; mid) &nbsp; entry = mid = 0.34 &nbsp; contracts = stake / 0.34</text>
        <text x="20" y="331" font-size="11" fill="var(--muted)">The $48B rung never competes: its mid 0.02 falls outside PRICE_BAND (0.03 &ndash; 0.97).</text>
      </g>
    </svg></div>
    <div class="cap">Illustrative ladder for one metric. The bar is the market&rsquo;s mid, the tick is
      ClaudeProphet&rsquo;s probability for the same threshold, the shaded band between them is the
      divergence. Exactly one rung &mdash; the widest band &mdash; becomes a position.</div>

    <h3>What the code actually does</h3>
    <ol>
      <li><b>Collect the rungs for one metric.</b>
        <span class="d"><code>contract_rows_for()</code> pulls every open contract that belongs to the
        same <i>(company, metric, period, resolve date)</i> group as the forecast, and parses each
        question back into its numeric threshold.</span></li>
      <li><b>Compare our probability against the market at each rung.</b>
        <span class="d">The forecast&rsquo;s <code>cp_thresholds</code> are indexed by threshold, so
        every contract yields a pair: <code>cp_p</code> (our probability) and <code>yes_mid</code>
        (the market&rsquo;s mid). The divergence is simply <code>diff = cp_p - yes_mid</code>.</span></li>
      <li><b>Throw away the near-settled tails.</b>
        <span class="d">A rung whose mid sits outside <code>PRICE_BAND = (0.03, 0.97)</code> is
        skipped before it can be chosen. Those contracts are effectively decided; a large percentage
        gap there is mostly noise, and the payoff is asymmetric.</span></li>
      <li><b>Keep only the single largest divergence.</b>
        <span class="d">Among the surviving rungs the one with the biggest <code>|diff|</code> wins.
        Not the sum, not an average across the ladder &mdash; one contract per metric.</span></li>
      <li><b>Require a real edge.</b>
        <span class="d">If that best rung still has <code>|diff| &lt; MIN_EDGE</code>
        (<code>0.05</code>, i.e. 5 percentage points) the whole metric is dropped and no position is
        opened. Agreeing with the market is not a trade.</span></li>
      <li><b>Choose the side and the entry price.</b>
        <span class="d"><code>side = YES</code> when our probability is above the mid, otherwise
        <code>NO</code>. A YES enters at the mid; a NO enters at <code>1 - mid</code>, because buying
        NO costs the complement. Position size is <code>contracts = stake / entry_price</code>, so a
        cheaper contract buys more of them.</span></li>
      <li><b>Size it from a fixed bankroll.</b>
        <span class="d"><code>init</code> counts the qualifying positions first and splits
        <code>BANKROLL = 1000.0</code> equally across them, storing the result as
        <code>stake_per_position</code>. The daily run uses <code>add</code>, which only opens
        positions for metrics not already in the ledger and reuses that stored stake, so a position
        opened in week six is the same size as one opened on day one.</span></li>
      <li><b>Mark and settle.</b>
        <span class="d"><code>mark</code> asks Kalshi for the settled markets in each series the
        ledger touches. When a held ticker comes back with a result it computes
        <code>won = (result == "yes") == (side == "YES")</code>,
        <code>payout = contracts * (1 if won else 0)</code> and
        <code>realized_pnl = payout - stake</code>, then flips the position to
        <code>resolved</code>. Positions still open are left alone &mdash; the dashboard marks
        those to the latest price pull when it regenerates this page.</span></li>
    </ol>

    <h3>The rules, in numbers</h3>
    <div class="formula">BANKROLL = 1000.0 &nbsp; MIN_EDGE = 0.05 &nbsp; PRICE_BAND = (0.03, 0.97)<br>
      pick = argmax |cp_p &minus; yes_mid| over rungs inside PRICE_BAND<br>
      side = YES if cp_p &gt; yes_mid else NO<br>
      entry_price = yes_mid (YES) &nbsp;|&nbsp; 1 &minus; yes_mid (NO)<br>
      contracts = stake / entry_price<br>
      open P&amp;L = contracts &times; (current_price &minus; entry_price)<br>
      realized_pnl = contracts &times; (1 if won else 0) &minus; stake</div>

    <div class="fig"><svg viewBox="0 0 400 362" role="img"
      aria-label="Position lifecycle: pick the widest-gap contract, enter at the mid, mark open positions to the latest price, settle when Kalshi reports a result, then record realized profit or loss in the ledger.">
      <defs>
        <marker id="stArrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--border-strong)"/>
        </marker>
      </defs>
      <g font-family="var(--font)">
        <!-- 1 pick -->
        <rect x="10" y="8" width="380" height="58" rx="10" fill="var(--accent-weak)" stroke="var(--accent)" stroke-width="1.5"/>
        <circle cx="36" cy="37" r="11" fill="var(--accent)"/>
        <text x="36" y="41" text-anchor="middle" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--surface)">1</text>
        <text x="56" y="29" font-size="12.5" font-weight="600" fill="var(--text)">Pick the widest gap</text>
        <text x="56" y="44" font-family="var(--mono)" font-size="11" fill="var(--muted)">argmax |cp_p &minus; yes_mid| on the ladder</text>
        <text x="56" y="58" font-family="var(--mono)" font-size="11" fill="var(--muted)">needs |edge| &ge; 0.05, mid in 0.03..0.97</text>
        <line x1="200" y1="66" x2="200" y2="80" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#stArrow)"/>
        <!-- 2 enter -->
        <rect x="10" y="82" width="380" height="58" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="36" cy="111" r="11" fill="var(--accent-weak)"/>
        <text x="36" y="115" text-anchor="middle" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--accent)">2</text>
        <text x="56" y="103" font-size="12.5" font-weight="600" fill="var(--text)">Enter at the mid</text>
        <text x="56" y="118" font-family="var(--mono)" font-size="11" fill="var(--muted)">YES &rarr; mid &nbsp; NO &rarr; 1 &minus; mid</text>
        <text x="56" y="132" font-family="var(--mono)" font-size="11" fill="var(--muted)">contracts = stake / entry_price</text>
        <line x1="200" y1="140" x2="200" y2="154" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#stArrow)"/>
        <!-- 3 mark -->
        <rect x="10" y="156" width="380" height="58" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="36" cy="185" r="11" fill="var(--accent-weak)"/>
        <text x="36" y="189" text-anchor="middle" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--accent)">3</text>
        <text x="56" y="177" font-size="12.5" font-weight="600" fill="var(--text)">Mark to market daily</text>
        <text x="56" y="192" font-family="var(--mono)" font-size="11" fill="var(--muted)">unrealized = n &times; (now &minus; entry)</text>
        <text x="56" y="206" font-family="var(--mono)" font-size="11" fill="var(--muted)">recomputed by gen_dashboard.py</text>
        <line x1="200" y1="214" x2="200" y2="228" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#stArrow)"/>
        <!-- 4 settle -->
        <rect x="10" y="230" width="380" height="58" rx="10" fill="var(--surface-2)" stroke="var(--border-strong)"/>
        <circle cx="36" cy="259" r="11" fill="var(--accent-weak)"/>
        <text x="36" y="263" text-anchor="middle" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--accent)">4</text>
        <text x="56" y="251" font-size="12.5" font-weight="600" fill="var(--text)">Settle when Kalshi reports</text>
        <text x="56" y="266" font-family="var(--mono)" font-size="11" fill="var(--muted)">portfolio.py mark &rarr; result yes / no</text>
        <text x="56" y="280" font-family="var(--mono)" font-size="11" fill="var(--muted)">won = (result == yes) == (side == YES)</text>
        <line x1="200" y1="288" x2="200" y2="302" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#stArrow)"/>
        <!-- 5 realize -->
        <rect x="10" y="304" width="380" height="58" rx="10" fill="var(--surface-2)" stroke="var(--accent)" stroke-width="1.5"/>
        <circle cx="36" cy="333" r="11" fill="var(--accent-weak)"/>
        <text x="36" y="337" text-anchor="middle" font-family="var(--mono)" font-size="11.5" font-weight="600" fill="var(--accent)">5</text>
        <text x="56" y="325" font-size="12.5" font-weight="600" fill="var(--text)">Book the realized P&amp;L</text>
        <text x="56" y="340" font-family="var(--mono)" font-size="11" fill="var(--muted)">payout = n if won else 0</text>
        <text x="56" y="354" font-family="var(--mono)" font-size="11" fill="var(--muted)">realized_pnl = payout &minus; stake &rarr; ledger</text>
      </g>
    </svg></div>
    <div class="cap">A position only ever moves one way down this list. <code>data/portfolio.json</code>
      is the single record of it; <code>scripts/daily.sh</code> runs <code>add</code> then
      <code>mark</code> each day.</div>

    <h3>Why bet a rung and not the number</h3>
    <p>A tempting alternative would be to trade the metric itself &mdash; if ClaudeProphet says $44.2B
      and the market implies $42.6B, buy &ldquo;higher&rdquo;. But Kalshi does not sell the number; it
      sells a dozen yes/no contracts about it, and they are not equally mispriced. Near the middle of
      the ladder a 1.5% difference in the forecast can move a contract 20 points; out in the tails the
      same difference moves it barely at all.</p>
    <p>So the strategy converts a view about a <i>number</i> into a bet on the one <i>contract</i>
      where that view implies the most disagreement in price terms. That rung is where any genuine
      edge is concentrated, and it is also the cleanest test of the forecast: if the model is right
      about the distribution, the contract it disagrees with most is the one it should make money on.
      Concentrating on one rung per metric also keeps the positions independent &mdash; two rungs of
      the same ladder are nearly the same bet twice.</p>
    <p>The flip side is honest: one contract per metric means each position is a coarse, roughly
      binary outcome, and the ladder&rsquo;s widest gap is also where a badly calibrated
      p10&ndash;p90 does the most damage. The point of the paper book is to find out which.</p>

    <h3>Where to see the results</h3>
    <ul class="plain">
      <li><b>Paper P&amp;L</b> &mdash; unrealized plus realized, the headline number for the whole
        book.</li>
      <li><b>Unrealized</b> &mdash; open positions marked to the latest price pull:
        <code>contracts &times; (current &minus; entry)</code>, summed.</li>
      <li><b>Realized</b> &mdash; settled positions only, summed <code>realized_pnl</code>. This is
        the number that cannot move again.</li>
      <li><b>Deployed</b> &mdash; stake currently tied up in open positions, out of the $1,000
        bankroll.</li>
      <li><b>Open positions</b> and <b>Record</b> &mdash; how many bets are live, and the
        wins&ndash;losses count on the ones that have settled.</li>
      <li><b>Cumulative realized P&amp;L</b> &mdash; the equity curve, starting at $0 with one step
        per settled position, in settlement order. Hover a dot for the company and that
        position&rsquo;s contribution.</li>
      <li><b>The positions table</b> &mdash; every bet with its side, entry price, our probability at
        entry, the current price and the running P&amp;L; settled rows carry a
        <i>settled yes/no</i> tag.</li>
    </ul>
    <p>All of it sits in the <b>Paper portfolio</b> panel on the Dashboard tab. Nothing on this page
      is a recommendation, and no trade is ever placed &mdash; the ledger exists to keep the
      forecasts honest.</p>
  </div>
  </section>
</div>

<script>
const DATA=__DATA__, MONTHS=__MONTHS__, STATS=__STATS__, PORT=__PORT__, TRACK=__TRACK__;
const PM_DATA=__PM_DATA__, PM_PORT=__PM_PORT__, PM_STATS=__PM_STATS__;
const root=document.documentElement;
function setTheme(t){root.setAttribute('data-theme',t);try{localStorage.setItem('kpi-theme',t);}catch(e){}}
(function(){let s=null;try{s=localStorage.getItem('kpi-theme');}catch(e){}setTheme(s||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'));})();
document.getElementById('tg').onclick=()=>setTheme(root.getAttribute('data-theme')==='dark'?'light':'dark');

/* ---- Tabs -------------------------------------------------------------
   Single source of truth: every <section class="tabpanel" data-tab="x"
   data-tab-label="X"> in the body becomes a tab, in document order. To add a
   tab, add one such <section> — no changes needed here. First section is the
   default; the last-viewed tab is remembered in localStorage. -------------- */
(function(){
  const bar=document.getElementById('tabs');
  const panels=[...document.querySelectorAll('.tabpanel[data-tab]')];
  if(!bar||!panels.length)return;
  const btns=panels.map(p=>{
    const b=document.createElement('button');
    b.type='button'; b.className='tab'; b.setAttribute('role','tab');
    b.dataset.tab=p.dataset.tab; b.id='tab-'+p.dataset.tab;
    b.textContent=p.dataset.tabLabel||p.dataset.tab;
    p.id='panel-'+p.dataset.tab;
    p.setAttribute('aria-labelledby',b.id); b.setAttribute('aria-controls',p.id);
    bar.appendChild(b); return b;
  });
  function show(name,focus){
    panels.forEach(p=>{p.hidden=(p.dataset.tab!==name);});
    btns.forEach(b=>{const on=b.dataset.tab===name;
      b.setAttribute('aria-selected',on?'true':'false'); b.tabIndex=on?0:-1;
      if(on&&focus)b.focus();});
    try{localStorage.setItem('kpi-tab',name);}catch(e){}
  }
  btns.forEach((b,i)=>{
    b.addEventListener('click',()=>show(b.dataset.tab));
    b.addEventListener('keydown',e=>{
      const d=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0;
      if(d){e.preventDefault();show(btns[(i+d+btns.length)%btns.length].dataset.tab,true);}
      else if(e.key==='Home'){e.preventDefault();show(btns[0].dataset.tab,true);}
      else if(e.key==='End'){e.preventDefault();show(btns[btns.length-1].dataset.tab,true);}
    });
  });
  let init=null; try{init=localStorage.getItem('kpi-tab');}catch(e){}
  show(panels.some(p=>p.dataset.tab===init)?init:panels[0].dataset.tab);
})();

document.getElementById('tiles').innerHTML=[
  ['Forecasted metrics',STATS.shown.toLocaleString(),'hl'],
  ['Contracts',STATS.contracts.toLocaleString(),''],
  ['Companies',STATS.companies,''],
  ['Paper positions',STATS.positions.toLocaleString(),''],
  ['Coverage',STATS.shown.toLocaleString()+' / '+STATS.total.toLocaleString(),'small'],
  ['Next resolution',STATS.next,'small'],
].map(t=>`<div class="tile ${t[2].includes('hl')?'hl':''}"><div class="lab">${t[0]}</div><div class="val ${t[2].replace('hl','').trim()}">${t[1]}</div></div>`).join('');

if(TRACK.length){
  document.getElementById('trackpanel').style.display='';
  const fm=v=>{if(v>=1e9)return (v/1e9).toFixed(2)+'B';if(v>=1e6)return (v/1e6).toFixed(2)+'M';if(v>=1e3)return Math.round(v/1e3)+'K';return String(v);};
  document.getElementById('trackbody').innerHTML=TRACK.map(t=>{
    const briers=[['ClaudeProphet',t.brier_cp,'var(--up)'],['Market',t.brier_market,'var(--down)']];
    if(t.brier_fs!=null)briers.push(['FutureSearch',t.brier_fs,'var(--fs)']);
    const win=briers.slice().sort((a,b)=>a[1]-b[1])[0];
    const cpWin=win[0]==='ClaudeProphet';
    return `<tr>
      <td><span style="font-weight:500">${t.co}</span> &mdash; ${t.metric}<span class="nc">${t.period}</span></td>
      <td class="num tnum">${fm(t.cp_median)}</td>
      <td class="num tnum">${fm(t.market_median)}</td>
      <td class="num tnum">${t.actual_range}</td>
      <td class="num tnum" style="font-weight:500;color:${cpWin?'var(--up)':'var(--text)'}">${t.brier_cp.toFixed(3)}</td>
      <td class="num tnum">${t.brier_market.toFixed(3)}</td>
      <td class="num tnum" style="color:var(--fs)">${t.brier_fs!=null?t.brier_fs.toFixed(3):'&mdash;'}</td>
      <td class="num" style="font-weight:600;color:${win[2]}">${win[0]}</td>
    </tr>`;}).join('');
}
function drawPnlInto(c, hostId, wrapId){
  const host=document.getElementById(hostId), wrap=document.getElementById(wrapId);
  c=c||[];
  if(!host||!wrap) return;
  if(c.length<2){ wrap.style.display='none'; return; }
  const W=760,H=220,padL=52,padR=58,padT=16,padB=30;
  const n=c.length, xs=c.map((_,i)=>padL+(W-padL-padR)*(n===1?0:i/(n-1)));
  const cums=c.map(p=>p.cum);
  let lo=Math.min(0,...cums), hi=Math.max(0,...cums);
  if(lo===hi){ lo-=1; hi+=1; }
  const pad=(hi-lo)*0.14; lo-=pad; hi+=pad;
  const y=v=>padT+(H-padT-padB)*(1-(v-lo)/(hi-lo));
  const line=xs.map((x,i)=>(i?'L':'M')+x.toFixed(1)+' '+y(cums[i]).toFixed(1)).join(' ');
  const y0=y(0), fin=cums[n-1], col=fin>=0?'var(--up)':'var(--down)';
  const fmt=v=>(v<0?'-$':'+$')+Math.abs(v).toFixed(0);
  const area=line+` L ${xs[n-1].toFixed(1)} ${y0.toFixed(1)} L ${xs[0].toFixed(1)} ${y0.toFixed(1)} Z`;
  const dots=c.map((p,i)=> i===0?'':`<circle cx="${xs[i].toFixed(1)}" cy="${y(p.cum).toFixed(1)}" r="3.5" fill="${col}"><title>${p.date} — ${p.co}: ${fmt(p.pnl)} (running ${fmt(p.cum)})</title></circle>`).join('');
  const svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;height:auto" role="img" aria-label="Cumulative realized P&L, ${fmt(fin)}">
    <line x1="${padL}" y1="${y0.toFixed(1)}" x2="${W-padR}" y2="${y0.toFixed(1)}" stroke="var(--border-strong)" stroke-dasharray="3 4"/>
    <text x="${padL-8}" y="${y0.toFixed(1)}" text-anchor="end" dominant-baseline="middle" fill="var(--faint)" font-size="11" font-family="var(--mono)">$0</text>
    <path d="${area}" fill="${col}" fill-opacity="0.11"/>
    <path d="${line}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}
    <text x="${(W-padR+8)}" y="${y(fin).toFixed(1)}" dominant-baseline="middle" fill="${col}" font-size="12.5" font-weight="600" font-family="var(--mono)">${fmt(fin)}</text>
    <text x="${xs[0].toFixed(1)}" y="${H-9}" text-anchor="start" fill="var(--faint)" font-size="11" font-family="var(--mono)">${c[0].date}</text>
    <text x="${xs[n-1].toFixed(1)}" y="${H-9}" text-anchor="end" fill="var(--faint)" font-size="11" font-family="var(--mono)">${c[n-1].date}</text>
  </svg>`;
  host.innerHTML=svg; wrap.style.display='';
}
function renderPortfolio(P, ids){
  const panel=document.getElementById(ids.panel);
  if(!P||!P.summary){ if(panel) panel.style.display='none'; return; }
  const s=P.summary, tot=s.unrealized+s.realized;
  const money=v=>(v<0?'-':'+')+'$'+Math.abs(v).toFixed(0);
  const cls=v=>v>=0?'style="color:var(--up)"':'style="color:var(--down)"';
  panel.style.display='';
  document.getElementById(ids.tiles).innerHTML=[
    ['Paper P&L',`<span ${cls(tot)}>${money(tot)}</span>`],
    ['Unrealized',`<span ${cls(s.unrealized)}>${money(s.unrealized)}</span>`],
    ['Realized',`<span ${cls(s.realized)}>${money(s.realized)}</span>`],
    ['Deployed','$'+s.deployed.toLocaleString()],
    ['Open positions',String(s.open)],
    ['Record',s.wins+s.losses?`${s.wins}W&ndash;${s.losses}L`:'&mdash;','small'],
  ].map(t=>`<div class="tile"><div class="lab">${t[0]}</div><div class="val ${t[2]||''}" style="font-size:20px">${t[1]}</div></div>`).join('');
  drawPnlInto(P.pnl_curve, ids.chart, ids.wrap);
  document.getElementById(ids.body).innerHTML=P.positions.map(p=>{
    const sideC=p.side==='YES'?'var(--yes)':'var(--no)';
    const pnl=p.pnl==null?'<span class="dash">&mdash;</span>':
      `<span class="tnum" style="color:${p.pnl>=0?'var(--up)':'var(--down)'};font-weight:500">${p.pnl>=0?'+':''}$${p.pnl.toFixed(0)}</span>`;
    const st=p.status==='resolved'?` <span class="pill" style="background:var(--track);color:var(--muted)">settled ${p.result}</span>`:'';
    return `<tr>
      <td><span style="color:${sideC};font-weight:600">${p.side}</span></td>
      <td><span style="font-weight:500">${p.co}</span> &mdash; ${p.metric}<span class="nc">${p.period}</span>${st}${p.url?` <a class="portml mktlink" href="${p.url}" target="_blank" rel="noopener" title="Live market">&#8599;</a>`:''}</td>
      <td class="num tnum">${p.r||''}</td>
      <td class="num tnum">${p.entry.toFixed(2)}</td>
      <td class="num tnum">${p.cp_p.toFixed(2)}</td>
      <td class="num tnum">${p.cur==null?'&mdash;':p.cur.toFixed(2)}</td>
      <td class="num">${pnl}</td>
    </tr>`;
  }).join('');
}
renderPortfolio(PORT, {panel:'portpanel',tiles:'porttiles',body:'portbody',chart:'pnlchart',wrap:'pnlwrap'});
renderPortfolio(typeof PM_PORT!=='undefined'?PM_PORT:null, {panel:'pm_portpanel',tiles:'pm_porttiles',body:'pm_portbody',chart:'pm_pnlchart',wrap:'pm_pnlwrap'});
// ---- Polymarket tiles + markets table ----
if(typeof PM_STATS!=='undefined'){
  const pt=document.getElementById('pm_tiles');
  if(pt) pt.innerHTML=[
    ['Earnings markets',PM_STATS.shown,'hl'],
    ['Forecasted',PM_STATS.forecasted+' / '+PM_STATS.shown,''],
    ['Paper positions',PM_STATS.positions,''],
    ['Next resolution',PM_STATS.next||'—','small'],
  ].map(t=>`<div class="tile"><div class="lab">${t[0]}</div><div class="val ${t[2]||''}">${t[1]}</div></div>`).join('');
}
function pmDetail(d){
  let h='';
  if(d.reason) h+=`<div class="reason"><b>ClaudeProphet:</b> ${d.reason}</div>`;
  if(d.side) h+=`<div class="betline"><span class="bs ${d.side}">${d.side}</span><span>our paper bet &middot; <a class="mktlink" href="${d.url}" target="_blank" rel="noopener">view live market on Polymarket &#8599;</a></span></div>`;
  if(!d.lad||!d.lad.length) return h||'<div class="ladtitle">binary beat/miss market</div>';
  h+='<div class="ladtitle">threshold &rarr; P(Yes)</div><div class="lad">';
  h+=`<div class="ladrow head"><span class="th">threshold</span><span></span><span class="pm">mkt</span><span class="pc">CP</span></div>`;
  h+=d.lad.map(x=>{const w=x.p==null?0:Math.round(x.p*100);const c=x.p>=0.5?'var(--yes)':'var(--no)';
    const tick=x.cp!=null?`<span class="cptick" style="left:${Math.round(x.cp*100)}%"></span>`:'';
    return `<div class="ladrow"><span class="th tnum">&ge; ${x.t}</span><div class="ladtrack"><div class="ladfill" style="width:${w}%;background:${c}"></div>${tick}</div><span class="pm tnum">${x.p==null?'-':w+'%'}</span><span class="pc tnum">${x.cp!=null?Math.round(x.cp*100)+'%':''}</span></div>`;}).join('');
  return h+'</div>';
}
if(typeof PM_DATA!=='undefined'){
  const pmtb=document.getElementById('pm_tb');
  if(pmtb){
    const kindLabel=k=>k==='binary'?'beat/miss':(k==='bucket_cdf'?'buckets':'ladder');
    pmtb.innerHTML=PM_DATA.map((d,i)=>{
      const per=d.period?`<span class="per">${d.period}</span>`:'';
      const link=d.url?` <a class="mktlink" href="${d.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Live Polymarket market">&#8599;</a>`:'';
      const edge=d.edge==null?'<span class="dash">&mdash;</span>':`<span class="edge ${d.edge>=0?'up':'down'} tnum">${d.edge_disp}</span>`;
      const pill=`<span class="pill" style="background:var(--track);color:var(--muted)">${kindLabel(d.kind)}</span>`;
      return `<tr class="grp ${d.our?'fc':''}" data-pi="${i}">
        <td class="co"><span class="chev">&#9656;</span> ${d.co}</td>
        <td class="metric">${d.metric}${per} ${pill}${link}</td>
        <td class="rd tnum">${d.close||'&mdash;'}</td>
        <td class="num"><span class="est tnum">${d.market||'&mdash;'}</span></td>
        <td class="num">${d.our?`<span class="cp tnum">${d.our}</span>`:'<span class="dash">&mdash;</span>'}</td>
        <td class="num">${edge}</td>
      </tr>`;}).join('');
    pmtb.querySelectorAll('.grp').forEach(tr=>{tr.onclick=()=>{
      const nx=tr.nextElementSibling;
      if(nx&&nx.classList.contains('detail')){nx.remove();tr.classList.remove('open');return;}
      tr.classList.add('open');const det=document.createElement('tr');det.className='detail';
      det.innerHTML=`<td colspan="6">${pmDetail(PM_DATA[tr.dataset.pi])}</td>`;tr.after(det);};});
  }
}
const maxM=Math.max(...MONTHS.map(m=>m[1]));
document.getElementById('tl').innerHTML=MONTHS.map(([mo,n])=>`<div class="tlrow"><span class="mo tnum">${mo}</span><div class="tlbar" style="width:${Math.max(2,Math.round(n/maxM*100))}%"></div><span class="n tnum">${n}</span></div>`).join('');
document.getElementById('mo').innerHTML='<option value="">All months</option>'+MONTHS.map(([mo])=>`<option value="${mo}">${mo}</option>`).join('');

let sortK='r',sortDir=1;
const q=document.getElementById('q'),mo=document.getElementById('mo'),lv=document.getElementById('lv'),tb=document.getElementById('tb');
function est(d){ if(d.med==null)return '<span class="dash">&mdash;</span>'; const op=d.medop==='~'?'&asymp; ':(d.medop+' '); return `<span class="est tnum">${op}${d.med}</span><span class="nc">${d.n}</span>`; }
function cpCell(d){ return d.cp==null?'<span class="dash">&mdash;</span>':`<span class="cp tnum">&asymp; ${d.cp}</span>`; }
function fsCell(d){ return d.fs==null?'<span class="dash">&mdash;</span>':`<span class="cp tnum" style="color:var(--fs)">&asymp; ${d.fs}</span>`; }
function edgeCell(d){ if(d.edge==null)return '<span class="dash">&mdash;</span>'; const c=d.edge>=0?'up':'down'; const s=d.edge>0?'+':''; return `<span class="edge ${c} tnum">${s}${d.edge}%</span>`; }
function detailHTML(d){
  let h='';
  if(d.reason){h+=`<div class="reason"><b>ClaudeProphet:</b> ${d.reason} <span style="color:var(--faint)">(p10&ndash;p90: ${d.cprange})</span></div>`;}
  if(d.fs_reason){h+=`<div class="reason"><b style="color:var(--fs)">FutureSearch:</b> ${d.fs_reason}</div>`;}
  if(d.bet){h+=`<div class="betline"><span class="bs ${d.bet.side}">${d.bet.side}</span><span>our paper bet &middot; <a class="mktlink" href="${d.bet.url}" target="_blank" rel="noopener">view live market on Kalshi &#8599;</a></span></div>`;}
  if(!d.lad.length)return h+'<div class="ladtitle">no numeric thresholds</div>';
  const hasCP=d.lad.some(x=>x.cp!=null), hasFS=d.lad.some(x=>x.fs!=null);
  h+='<div class="ladtitle">threshold &rarr; P(Yes)</div><div class="lad">';
  h+=`<div class="ladrow head"><span class="th">threshold</span><span></span><span class="pm">mkt</span><span class="pc">${hasCP?'CP':''}</span><span class="pf">${hasFS?'FS':''}</span></div>`;
  h+=d.lad.map(x=>{const w=x.p==null?0:Math.round(x.p*100);const c=x.p>=0.5?'var(--yes)':'var(--no)';
    const tick=x.cp!=null?`<span class="cptick" style="left:${Math.round(x.cp*100)}%"></span>`:'';
    const ftick=x.fs!=null?`<span class="fstick" style="left:${Math.round(x.fs*100)}%"></span>`:'';
    return `<div class="ladrow"><span class="th tnum">&ge; ${x.t}</span><div class="ladtrack"><div class="ladfill" style="width:${w}%;background:${c}"></div>${tick}${ftick}</div><span class="pm tnum">${x.p==null?'-':w+'%'}</span><span class="pc tnum">${x.cp!=null?Math.round(x.cp*100)+'%':''}</span><span class="pf tnum">${x.fs!=null?Math.round(x.fs*100)+'%':''}</span></div>`;}).join('');
  return h+'</div>';
}
function view(){
  const term=q.value.trim().toLowerCase(),m=mo.value,unc=lv.checked;
  let rows=DATA.map((d,i)=>({d,i})).filter(({d})=>{
    if(m&&(d.r||'').slice(0,7)!==m)return false;
    if(unc&&d.medop!=='~')return false;
    if(term&&!(d.co.toLowerCase().includes(term)||d.metric.toLowerCase().includes(term)))return false;
    return true;});
  rows.sort((a,b)=>{let x,y;
    if(sortK==='cp'){x=a.d.cp==null?-1:parseFloat(a.d.cp);y=b.d.cp==null?-1:parseFloat(b.d.cp);}
    else if(sortK==='fs'){x=a.d.fs==null?-1:parseFloat(a.d.fs);y=b.d.fs==null?-1:parseFloat(b.d.fs);}
    else if(sortK==='edge'){x=a.d.edge==null?-999:a.d.edge;y=b.d.edge==null?-999:b.d.edge;}
    else if(sortK==='med'){x=a.d.med==null?-1:parseFloat(a.d.med);y=b.d.med==null?-1:parseFloat(b.d.med);}
    else{x=a.d[sortK];y=b.d[sortK];}
    if(typeof x==='string')return x.localeCompare(y)*sortDir;return((x||0)-(y||0))*sortDir;});
  document.getElementById('cnt').textContent=rows.length.toLocaleString()+' of '+DATA.length.toLocaleString()+' metrics';
  tb.innerHTML=rows.map(({d,i})=>{const per=d.period?`<span class="per">${d.period}</span>`:'';
    return `<tr class="grp ${d.cp?'fc':''}" data-i="${i}">
      <td class="co"><span class="chev">&#9656;</span> ${d.co}</td>
      <td class="metric">${d.metric}${per}${d.bet?` <a class="mktlink" href="${d.bet.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Live Kalshi market we'd bet">&#8599;</a>`:''}</td>
      <td class="rd tnum">${d.r}</td>
      <td class="num">${est(d)}</td>
      <td class="num">${cpCell(d)}</td>
      <td class="num">${fsCell(d)}</td>
      <td class="num">${edgeCell(d)}</td>
    </tr>`;}).join('');
  tb.querySelectorAll('.grp').forEach(tr=>{tr.onclick=()=>{
    const nx=tr.nextElementSibling;
    if(nx&&nx.classList.contains('detail')){nx.remove();tr.classList.remove('open');return;}
    tr.classList.add('open');const det=document.createElement('tr');det.className='detail';
    det.innerHTML=`<td colspan="7">${detailHTML(DATA[tr.dataset.i])}</td>`;tr.after(det);};});
}
document.querySelectorAll('#ftable thead th').forEach(th=>{th.onclick=()=>{const k=th.dataset.k;
  if(!k)return;
  if(k===sortK)sortDir*=-1;else{sortK=k;sortDir=1;}
  document.querySelectorAll('#ftable thead th').forEach(t=>{t.classList.remove('sorted');const a=t.querySelector('.ar');if(a)a.innerHTML='&#8597;';});
  th.classList.add('sorted');const a=th.querySelector('.ar');if(a)a.innerHTML=sortDir>0?'&#8593;':'&#8595;';view();};});
q.oninput=view;mo.onchange=view;lv.onchange=view;view();

// Make the portfolio / track-record tables sortable too (DOM sort,
// numeric-aware). The main forecasts table (#ftable) keeps its own data sort above.
function makeSortable(table){
  const ths=[...table.querySelectorAll('thead th')]; const dirs=ths.map(()=>1);
  const num=s=>{const m=s.replace(/[,$%]/g,'').match(/-?\\d+\\.?\\d*/);return m?parseFloat(m[0]):null;};
  ths.forEach((th,i)=>{ th.style.cursor='pointer';
    th.addEventListener('click',()=>{
      const tb=table.querySelector('tbody'); const rows=[...tb.querySelectorAll('tr')];
      rows.sort((a,b)=>{ const x=(a.children[i]||{}).textContent||'', y=(b.children[i]||{}).textContent||'';
        const nx=num(x), ny=num(y);
        return ((nx!=null&&ny!=null)?nx-ny:x.trim().localeCompare(y.trim()))*dirs[i]; });
      dirs[i]*=-1; rows.forEach(r=>tb.appendChild(r));
      ths.forEach(t=>t.classList.remove('sorted')); th.classList.add('sorted');
    });
  });
}
['portbody','trackbody'].forEach(id=>{ const tb=document.getElementById(id);
  if(tb&&tb.children.length){const t=tb.closest('table'); if(t)makeSortable(t);} });
</script>"""

html=(HTML.replace("__DATA__",DATA_JSON).replace("__MONTHS__",MONTHS_JSON)
          .replace("__STATS__",STATS_JSON).replace("__PORT__",PORT_JSON)
          .replace("__TRACK__",TRACK_JSON)
          .replace("__PM_DATA__",PM_DATA_JSON).replace("__PM_PORT__",PM_PORT_JSON)
          .replace("__PM_STATS__",PM_STATS_JSON)
          .replace("__PSTAKE__",str(int(portfolio["summary"]["stake"]) if portfolio.get("summary") else 100))
          .replace("__SNAP__",SNAP))
OUT.write_text(html,encoding="utf-8")
print(f"wrote {OUT} ({len(html)} bytes) | shown={stats['shown']}/{stats['total']} metrics, {stats['positions']} positions")
