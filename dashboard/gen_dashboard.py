import json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import parse, fmtv, implied_median, group_markets, OPEN_MARKETS, FORECASTS

ROOT = Path(__file__).resolve().parents[1]
SRC = OPEN_MARKETS
FCST = FORECASTS
OUT = ROOT / "docs" / "index.html"
SNAP = "2026-07-22"

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

groups = group_markets(rows)

data=[]; nf=0
for (co,metric,period,r), g in groups.items():
    mk = g["markets"]; vol=sum(m[2] for m in mk)
    ladder=[(m[0],m[1]) for m in mk if m[0] is not None]
    imp=implied_median(ladder); med=None; medop=""
    if imp: medop,medv=imp; med=fmtv(medv)
    fc = fdict.get((co.lower(), metric.lower(), period, r))
    lad_disp=[]
    for v,p in sorted(ladder):
        e={"t":fmtv(v),"tv":v,"p":p}
        if fc and round(v) in fc["cp_thr"]: e["cp"]=fc["cp_thr"][round(v)]
        lad_disp.append(e)
    rec={"co":co,"metric":metric if period else (co+" KPI"),"period":period,"r":r,
         "n":len(mk),"v":vol,"med":med,"medop":medop,"lad":lad_disp,
         "cp":None,"edge":None,"reason":None,"cprange":None}
    if fc and imp:
        nf+=1
        rec["cp"]=fmtv(fc["cp_median"]); rec["cp_op"]="~"
        rec["cprange"]=fmtv(fc["cp_p10"])+" .. "+fmtv(fc["cp_p90"])
        rec["reason"]=fc["reason"]
        rec["edge"]=round((fc["cp_median"]-medv)/medv*100,1) if medv else None
    data.append(rec)

data.sort(key=lambda d:(0 if d["cp"] else 1, d["r"], d["co"]))  # forecasted first
companies=sorted({d["co"] for d in data})
cts=[d["r"] for d in data if d["r"]]
months=Counter(d["r"][:7] for d in data if d["r"])
live=sum(1 for d in data if d["medop"]=="~")
stats={"markets":len(rows),"metrics":len(data),"companies":len(companies),"live":live,
       "next":min(cts) if cts else "-","forecasts":nf}

# ---- paper portfolio: mark open positions to the latest pull ----
price_by_ticker={r["ticker"]:r.get("yes_mid") for r in rows}
PORT_PATH=ROOT/"data"/"portfolio.json"
portfolio={"positions":[],"summary":None}
if PORT_PATH.exists():
    led=json.loads(PORT_PATH.read_text())
    pos_out=[]; unreal=0.0; realized=0.0; wins=0; losses=0; deployed=0.0
    for p in led["positions"]:
        cur_yes=price_by_ticker.get(p["ticker"])
        row={"co":p["co"],"metric":p["metric"],"period":p["period"],"r":p["resolves"],
             "q":p["question"],"side":p["side"],"entry":p["entry_price"],
             "cp_p":p["cp_p"],"mkt_entry":p["entry_yes_mid"],"stake":p["stake"],
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
    portfolio={"positions":pos_out,
               "summary":{"deployed":round(deployed,2),"unrealized":round(unreal,2),
                          "realized":round(realized,2),
                          "open":sum(1 for x in pos_out if x["status"]=="open"),
                          "wins":wins,"losses":losses,
                          "stake":led.get("stake_per_position",100),
                          "created":led.get("created","")[:10]}}

# ---- track record: scored resolved forecasts ----
SCORES=ROOT/"data"/"forecasts"/"resolved_scores.jsonl"
track=[json.loads(l) for l in SCORES.open()] if SCORES.exists() else []

# ---- backtest portfolio (last week, blind, $1k) ----
BT_PATH=ROOT/"data"/"backtest_portfolio.json"
backtest=json.loads(BT_PATH.read_text()) if BT_PATH.exists() else {"summary":None,"positions":[]}

DATA_JSON=json.dumps(data,separators=(",",":"))
MONTHS_JSON=json.dumps(sorted(months.items()))
STATS_JSON=json.dumps(stats)
PORT_JSON=json.dumps(portfolio,separators=(",",":"))
TRACK_JSON=json.dumps(track,separators=(",",":"))
BT_JSON=json.dumps(backtest,separators=(",",":"))

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
.ladrow{display:grid;grid-template-columns:118px 1fr 38px 38px;align-items:center;gap:9px;font-size:12px}
.ladrow.head{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em}
.ladrow .th{text-align:right;color:var(--muted)}
.ladtrack{height:8px;background:var(--track);border-radius:4px;overflow:hidden;position:relative}
.ladfill{height:100%;border-radius:4px;position:absolute;top:0;left:0}
.cptick{position:absolute;top:-2px;width:2px;height:12px;background:var(--accent)}
.ladrow .pm{text-align:right;color:var(--muted)}.ladrow .pc{text-align:right;color:var(--accent);font-weight:500}
.foot{color:var(--faint);font-size:12px;margin-top:14px;line-height:1.6}.foot b{color:var(--muted);font-weight:500}
</style>

<div class="wrap">
  <div class="top">
    <div><h1>Company-KPI open markets</h1>
      <div class="sub">Kalshi &middot; one row per forecastable metric &middot; ClaudeProphet forecasts (live research) &middot; snapshot __SNAP__</div></div>
    <button class="toggle" id="tg" aria-label="Toggle theme">Theme</button>
  </div>
  <div class="tiles" id="tiles"></div>

  <div class="panel" id="btpanel" style="display:none">
    <h2>Backtest portfolio &mdash; last week, blind forecasts, $1k start</h2>
    <div class="tiles" id="bttiles" style="margin-bottom:14px"></div>
    <div class="tblwrap" style="border-radius:10px"><table>
      <thead><tr>
        <th>Side</th><th>Position</th><th class="num">Resolved</th>
        <th class="num">Entry</th><th class="num">Our P</th><th class="num">Mkt P</th>
        <th class="num">Result</th><th class="num">P&amp;L</th>
      </tr></thead>
      <tbody id="btbody"></tbody>
    </table></div>
    <div class="foot" style="margin-top:10px">
      Retroactive but leakage-free: forecasts made with web access disabled (model
      knowledge ends Jan 2026, before every outcome), entered at the last real trade
      price &ge;24h before each market closed, settled at the actual result. Same
      position rule as the live book; $1,000 split equally across positions.
    </div>
  </div>

  <div class="panel" id="trackpanel" style="display:none">
    <h2>Track record &mdash; resolved forecasts</h2>
    <div class="tblwrap" style="border-radius:10px"><table>
      <thead><tr>
        <th>Metric</th><th class="num">ClaudeProphet</th><th class="num">Market</th>
        <th class="num">Actual</th><th class="num">CP Brier</th><th class="num">Mkt Brier</th><th class="num">Winner</th>
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
    <label class="chk"><input type="checkbox" id="fo"> Forecasted only</label>
    <span class="count" id="cnt"></span>
  </div>
  <div class="tblwrap"><table>
    <thead><tr>
      <th data-k="co">Company <span class="ar">&#8597;</span></th>
      <th data-k="metric">Metric <span class="ar">&#8597;</span></th>
      <th data-k="r" class="sorted">Resolves <span class="ar">&#8593;</span></th>
      <th data-k="med" class="num">Market est. <span class="ar">&#8597;</span></th>
      <th data-k="cp" class="num">ClaudeProphet <span class="ar">&#8597;</span></th>
      <th data-k="edge" class="num">Edge <span class="ar">&#8597;</span></th>
    </tr></thead>
    <tbody id="tb"></tbody>
  </table></div>
  <div class="foot">
    <b>Market est.</b> = market-implied central value (50% threshold crossing). <b>ClaudeProphet</b> = our live-researched median forecast of the reported figure. <b>Edge</b> = ClaudeProphet vs market, % of the metric. Click a forecasted row for the reasoning and a threshold-by-threshold market-vs-ClaudeProphet comparison.
  </div>
</div>

<script>
const DATA=__DATA__, MONTHS=__MONTHS__, STATS=__STATS__, PORT=__PORT__, TRACK=__TRACK__, BT=__BT__;
const root=document.documentElement;
function setTheme(t){root.setAttribute('data-theme',t);try{localStorage.setItem('kpi-theme',t);}catch(e){}}
(function(){let s=null;try{s=localStorage.getItem('kpi-theme');}catch(e){}setTheme(s||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'));})();
document.getElementById('tg').onclick=()=>setTheme(root.getAttribute('data-theme')==='dark'?'light':'dark');

document.getElementById('tiles').innerHTML=[
  ['Metrics',STATS.metrics.toLocaleString(),''],
  ['Contracts',STATS.markets.toLocaleString(),''],
  ['Companies',STATS.companies,''],
  ['Uncertain',STATS.live.toLocaleString(),''],
  ['Forecasts run',STATS.forecasts+' / '+STATS.metrics,'small hl'],
  ['Next resolution',STATS.next,'small'],
].map(t=>`<div class="tile ${t[2].includes('hl')?'hl':''}"><div class="lab">${t[0]}</div><div class="val ${t[2].replace('hl','').trim()}">${t[1]}</div></div>`).join('');

if(BT.summary){
  const s=BT.summary;
  document.getElementById('btpanel').style.display='';
  const money=v=>(v<0?'-':'+')+'$'+Math.abs(v).toFixed(0);
  const cls=v=>v>=0?'style="color:var(--up)"':'style="color:var(--down)"';
  document.getElementById('bttiles').innerHTML=[
    ['Bankroll','$'+s.bankroll_start.toFixed(0)+' &rarr; <span '+cls(s.realized_pnl)+'>$'+s.bankroll_end.toFixed(0)+'</span>','small'],
    ['Return',`<span ${cls(s.realized_pnl)}>${(s.return_pct>=0?'+':'')+s.return_pct}%</span>`],
    ['Record',`${s.wins}W&ndash;${s.losses}L`],
    ['Week Brier &mdash; CP',s.week_brier_cp!=null?s.week_brier_cp.toFixed(3):'&mdash;','small'],
    ['Week Brier &mdash; market',s.week_brier_market!=null?s.week_brier_market.toFixed(3):'&mdash;','small'],
    ['All resolved Brier',s.alltime_brier_cp!=null?`CP ${s.alltime_brier_cp.toFixed(3)} vs mkt ${s.alltime_brier_market.toFixed(3)}`:'&mdash;','small'],
  ].map(t=>`<div class="tile"><div class="lab">${t[0]}</div><div class="val ${t[2]||''}" style="font-size:${t[2]?'15px':'22px'}">${t[1]}</div></div>`).join('');
  document.getElementById('btbody').innerHTML=BT.positions.map(p=>{
    const sideC=p.side==='YES'?'var(--yes)':'var(--no)';
    return `<tr>
      <td><span style="color:${sideC};font-weight:600">${p.side}</span></td>
      <td><span style="font-weight:500">${p.co}</span> &mdash; ${p.metric}<span class="nc">${p.period}</span></td>
      <td class="num tnum">${p.resolves}</td>
      <td class="num tnum">${p.entry.toFixed(2)}</td>
      <td class="num tnum">${p.cp_p.toFixed(2)}</td>
      <td class="num tnum">${p.mkt_pre.toFixed(2)}</td>
      <td class="num tnum">${p.result.toUpperCase()}</td>
      <td class="num"><span class="tnum" style="color:${p.pnl>=0?'var(--up)':'var(--down)'};font-weight:500">${p.pnl>=0?'+':''}$${p.pnl.toFixed(0)}</span></td>
    </tr>`;}).join('');
}
if(TRACK.length){
  document.getElementById('trackpanel').style.display='';
  const fm=v=>{if(v>=1e9)return (v/1e9).toFixed(2)+'B';if(v>=1e6)return (v/1e6).toFixed(2)+'M';if(v>=1e3)return Math.round(v/1e3)+'K';return String(v);};
  document.getElementById('trackbody').innerHTML=TRACK.map(t=>{
    const cpWin=t.brier_cp<t.brier_market;
    return `<tr>
      <td><span style="font-weight:500">${t.co}</span> &mdash; ${t.metric}<span class="nc">${t.period}</span></td>
      <td class="num tnum">${fm(t.cp_median)}</td>
      <td class="num tnum">${fm(t.market_median)}</td>
      <td class="num tnum">${t.actual_range}</td>
      <td class="num tnum" style="font-weight:500;color:${cpWin?'var(--up)':'var(--text)'}">${t.brier_cp.toFixed(3)}</td>
      <td class="num tnum">${t.brier_market.toFixed(3)}</td>
      <td class="num" style="font-weight:600;color:${cpWin?'var(--up)':'var(--down)'}">${cpWin?'ClaudeProphet':'Market'}</td>
    </tr>`;}).join('');
}
if(PORT.summary){
  const s=PORT.summary, tot=s.unrealized+s.realized;
  const money=v=>(v<0?'-':'+')+'$'+Math.abs(v).toFixed(0);
  const cls=v=>v>=0?'style="color:var(--up)"':'style="color:var(--down)"';
  document.getElementById('portpanel').style.display='';
  document.getElementById('porttiles').innerHTML=[
    ['Paper P&L',`<span ${cls(tot)}>${money(tot)}</span>`],
    ['Unrealized',`<span ${cls(s.unrealized)}>${money(s.unrealized)}</span>`],
    ['Realized',`<span ${cls(s.realized)}>${money(s.realized)}</span>`],
    ['Deployed','$'+s.deployed.toLocaleString()],
    ['Open positions',String(s.open)],
    ['Record',s.wins+s.losses?`${s.wins}W&ndash;${s.losses}L`:'&mdash;','small'],
  ].map(t=>`<div class="tile"><div class="lab">${t[0]}</div><div class="val ${t[2]||''}" style="font-size:20px">${t[1]}</div></div>`).join('');
  document.getElementById('portbody').innerHTML=PORT.positions.map(p=>{
    const sideC=p.side==='YES'?'var(--yes)':'var(--no)';
    const pnl=p.pnl==null?'<span class="dash">&mdash;</span>':
      `<span class="tnum" style="color:${p.pnl>=0?'var(--up)':'var(--down)'};font-weight:500">${p.pnl>=0?'+':''}$${p.pnl.toFixed(0)}</span>`;
    const st=p.status==='resolved'?` <span class="pill" style="background:var(--track);color:var(--muted)">settled ${p.result}</span>`:'';
    return `<tr>
      <td><span style="color:${sideC};font-weight:600">${p.side}</span></td>
      <td><span style="font-weight:500">${p.co}</span> &mdash; ${p.metric}<span class="nc">${p.period}</span>${st}</td>
      <td class="num tnum">${p.r}</td>
      <td class="num tnum">${p.entry.toFixed(2)}</td>
      <td class="num tnum">${p.cp_p.toFixed(2)}</td>
      <td class="num tnum">${p.cur==null?'&mdash;':p.cur.toFixed(2)}</td>
      <td class="num">${pnl}</td>
    </tr>`;
  }).join('');
}
const maxM=Math.max(...MONTHS.map(m=>m[1]));
document.getElementById('tl').innerHTML=MONTHS.map(([mo,n])=>`<div class="tlrow"><span class="mo tnum">${mo}</span><div class="tlbar" style="width:${Math.max(2,Math.round(n/maxM*100))}%"></div><span class="n tnum">${n}</span></div>`).join('');
document.getElementById('mo').innerHTML='<option value="">All months</option>'+MONTHS.map(([mo])=>`<option value="${mo}">${mo}</option>`).join('');

let sortK='r',sortDir=1;
const q=document.getElementById('q'),mo=document.getElementById('mo'),lv=document.getElementById('lv'),fo=document.getElementById('fo'),tb=document.getElementById('tb');
function est(d){ if(d.med==null)return '<span class="dash">&mdash;</span>'; const op=d.medop==='~'?'&asymp; ':(d.medop+' '); return `<span class="est tnum">${op}${d.med}</span><span class="nc">${d.n}</span>`; }
function cpCell(d){ return d.cp==null?'<span class="dash">&mdash;</span>':`<span class="cp tnum">&asymp; ${d.cp}</span>`; }
function edgeCell(d){ if(d.edge==null)return '<span class="dash">&mdash;</span>'; const c=d.edge>=0?'up':'down'; const s=d.edge>0?'+':''; return `<span class="edge ${c} tnum">${s}${d.edge}%</span>`; }
function detailHTML(d){
  let h='';
  if(d.reason){h+=`<div class="reason"><b>ClaudeProphet:</b> ${d.reason} <span style="color:var(--faint)">(p10&ndash;p90: ${d.cprange})</span></div>`;}
  if(!d.lad.length)return h+'<div class="ladtitle">no numeric thresholds</div>';
  const hasCP=d.lad.some(x=>x.cp!=null);
  h+='<div class="ladtitle">threshold &rarr; P(Yes)</div><div class="lad">';
  h+=`<div class="ladrow head"><span class="th">threshold</span><span></span><span class="pm">mkt</span><span class="pc">${hasCP?'CP':''}</span></div>`;
  h+=d.lad.map(x=>{const w=x.p==null?0:Math.round(x.p*100);const c=x.p>=0.5?'var(--yes)':'var(--no)';
    const tick=x.cp!=null?`<span class="cptick" style="left:${Math.round(x.cp*100)}%"></span>`:'';
    return `<div class="ladrow"><span class="th tnum">&ge; ${x.t}</span><div class="ladtrack"><div class="ladfill" style="width:${w}%;background:${c}"></div>${tick}</div><span class="pm tnum">${x.p==null?'-':w+'%'}</span><span class="pc tnum">${x.cp!=null?Math.round(x.cp*100)+'%':''}</span></div>`;}).join('');
  return h+'</div>';
}
function view(){
  const term=q.value.trim().toLowerCase(),m=mo.value,unc=lv.checked,fon=fo.checked;
  let rows=DATA.map((d,i)=>({d,i})).filter(({d})=>{
    if(m&&(d.r||'').slice(0,7)!==m)return false;
    if(unc&&d.medop!=='~')return false;
    if(fon&&d.cp==null)return false;
    if(term&&!(d.co.toLowerCase().includes(term)||d.metric.toLowerCase().includes(term)))return false;
    return true;});
  rows.sort((a,b)=>{let x,y;
    if(sortK==='cp'){x=a.d.cp==null?-1:parseFloat(a.d.cp);y=b.d.cp==null?-1:parseFloat(b.d.cp);}
    else if(sortK==='edge'){x=a.d.edge==null?-999:a.d.edge;y=b.d.edge==null?-999:b.d.edge;}
    else if(sortK==='med'){x=a.d.med==null?-1:parseFloat(a.d.med);y=b.d.med==null?-1:parseFloat(b.d.med);}
    else{x=a.d[sortK];y=b.d[sortK];}
    if(typeof x==='string')return x.localeCompare(y)*sortDir;return((x||0)-(y||0))*sortDir;});
  document.getElementById('cnt').textContent=rows.length.toLocaleString()+' of '+DATA.length.toLocaleString()+' metrics';
  tb.innerHTML=rows.map(({d,i})=>{const per=d.period?`<span class="per">${d.period}</span>`:'';
    return `<tr class="grp ${d.cp?'fc':''}" data-i="${i}">
      <td class="co"><span class="chev">&#9656;</span> ${d.co}</td>
      <td class="metric">${d.metric}${per}</td>
      <td class="rd tnum">${d.r}</td>
      <td class="num">${est(d)}</td>
      <td class="num">${cpCell(d)}</td>
      <td class="num">${edgeCell(d)}</td>
    </tr>`;}).join('');
  tb.querySelectorAll('.grp').forEach(tr=>{tr.onclick=()=>{
    const nx=tr.nextElementSibling;
    if(nx&&nx.classList.contains('detail')){nx.remove();tr.classList.remove('open');return;}
    tr.classList.add('open');const det=document.createElement('tr');det.className='detail';
    det.innerHTML=`<td colspan="6">${detailHTML(DATA[tr.dataset.i])}</td>`;tr.after(det);};});
}
document.querySelectorAll('thead th').forEach(th=>{th.onclick=()=>{const k=th.dataset.k;
  if(k===sortK)sortDir*=-1;else{sortK=k;sortDir=1;}
  document.querySelectorAll('thead th').forEach(t=>{t.classList.remove('sorted');t.querySelector('.ar').innerHTML='&#8597;';});
  th.classList.add('sorted');th.querySelector('.ar').innerHTML=sortDir>0?'&#8593;':'&#8595;';view();};});
q.oninput=view;mo.onchange=view;lv.onchange=view;fo.onchange=view;view();
</script>"""

html=(HTML.replace("__DATA__",DATA_JSON).replace("__MONTHS__",MONTHS_JSON)
          .replace("__STATS__",STATS_JSON).replace("__PORT__",PORT_JSON)
          .replace("__TRACK__",TRACK_JSON).replace("__BT__",BT_JSON)
          .replace("__PSTAKE__",str(int(portfolio["summary"]["stake"]) if portfolio.get("summary") else 100))
          .replace("__SNAP__",SNAP))
OUT.write_text(html,encoding="utf-8")
print(f"wrote {OUT} ({len(html)} bytes) | metrics={stats['metrics']} forecasts={stats['forecasts']}")
