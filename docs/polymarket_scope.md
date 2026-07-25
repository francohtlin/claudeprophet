# Scope — Adding Polymarket as a company-report / KPI source

Status: **scoped + spiked** (2026-07-24). No changes to the live pipeline.
Spike code: `forecasting/sources/`, `forecasting/spike_polymarket.py`.

## 1. Goal

Forecast the same class of thing we already do for Kalshi — company reports and
KPIs (revenue, deliveries, subscribers, earnings beats) — but sourced from
**Polymarket** as well, feeding the same selection → forecast → paper-portfolio →
dashboard pipeline.

## 2. Feasibility (verified live against Gamma)

Polymarket carries relevant markets in **three shapes**:

| Shape | Example | Structure | Fit |
|-------|---------|-----------|-----|
| **A. Cumulative ladder** | "Apple Q3 Greater China revenue be above __?" | monotone `≥` thresholds | direct — same as Kalshi |
| **B. Range buckets** | "How many Tesla deliveries?" (`<300k … 475k+`) | exclusive buckets, prices sum ≈1 | needs bucket→CDF conversion |
| **C. Single binary** | "Will Starbucks beat quarterly earnings?" | one Yes/No (EPS threshold in slug) | one-rung metric |

Out of scope (different resolution model): **mention markets** ("What will
\<company\> *say during* their earnings call") — categorical / multi-true, filtered
out at discovery.

## 3. Architecture decision — one repo, provider modules (not separate repos)

Keep everything in this repo. Split by **provider module**, not git repo, because
the pipeline is tightly coupled around one shared row contract (`kpi_metrics.py`
+ everything downstream). Separate repos would turn that schema into a
cross-repo published contract to version, and a one-line schema change would span
three repos. The provider pattern already exists (`market_lookup/providers/`).

Extract to a package/repo only when a concrete trigger appears: external reuse,
independent deploy cadence/ownership, or heavy conflicting dependencies.

The seam is `forecasting/sources/`:

```
forecasting/sources/
  base.py        # Source protocol + UnifiedRow schema + normalize_company()
  kalshi.py      # KalshiSource (spike: reads local jsonl; MVP: moves the live pull here)
  polymarket.py  # PolymarketSource: Gamma discovery + A/B/C normalization + bucket→CDF
```

## 4. Design decisions (locked in)

- **Buckets → cumulative CDF.** `P(value ≥ edge) = Σ price(bucket) for buckets
  whose lower edge ≥ edge`. Makes shape B look like a monotone `≥` ladder so
  `select_kpi`, `forecast_kpi`, and `portfolio` need **zero** changes. Guarded:
  only convert when bucket prices sum to ≈1 and edges are contiguous; malformed
  sets are logged and skipped.
- **Cross-source: allow both, show venue spread.** No dedup. Group by
  `(company, metric, period)` *across* source so the dashboard can pair the two
  venues' ladders and display the implied-median spread. Each position carries
  its `source`. The metric-alias join across venues is the fuzzy part (see §7).

## 5. Unified row schema (backward-compatible)

```jsonc
{
  "source": "kalshi" | "polymarket",
  "company": "Apple", "metric": "Greater China revenue", "period": "Q3 2026",
  "question": "...", "threshold": 18000000000.0, "yes_mid": 0.76,
  "volume": 1234.0, "close_time": "2026-07-29T...",
  "ticker": "<kalshi ticker | polymarket conditionId>",
  "market_url": "https://polymarket.com/event/<slug>",
  "outcome_kind": "threshold" | "bucket_cdf" | "binary"
}
```
Existing Kalshi rows map in for free (`source:"kalshi"`, `outcome_kind:"threshold"`).

## 6. Component plan

| Component | File | Change |
|-----------|------|--------|
| Ingest (PM) | `forecasting/sources/polymarket.py` | **new** — Gamma `/public-search` over curated terms; filter open + company-report-shaped; normalize A/B/C |
| Ingest (Kalshi) | `forecasting/sources/kalshi.py` | move the live pull from `pull_kpi_markets.py` behind `discover()` (spike reads the local file) |
| Orchestrate | `forecasting/pull_all.py` | **new** — run every registered Source, write merged `company_kpi_open.jsonl` |
| Parse/group | `forecasting/kpi_metrics.py` | prefer an explicit `threshold` field on rows over re-parsing `question`; add a monotonicity clamp |
| Select/forecast | `select_kpi.py`, `forecast_kpi.py` | none (source-agnostic once rows are ladders) |
| Settle | `forecasting/portfolio.py` | split `mark` → `_mark_kalshi` / `_mark_polymarket` (PM: `closed` + `outcomePrices`→0/1), dispatch on position `source` |
| Dashboard | `dashboard/gen_dashboard.py` | `market_url` already per-row; add a source badge + filter; add a cross-venue spread view |

## 7. Risks & open items

- **Bucket edge cases:** open tails (`<300k`, `475k+`), gapped/overlapping bucket
  sets. v1 validates sum≈1 and contiguity, skips the rest.
- **Live-market noise:** thin PM ladders can be non-monotone (spike saw Apple
  `≥20B 0.25` then `≥21B 0.335`). Add an isotonic/monotone clamp before selection.
- **Cross-venue metric aliasing:** venues label the same KPI differently
  ("Total Deliveries" vs "deliveries"); periods differ (Kalshi "2026" vs PM
  "Q3"). Needs a light alias/period-normalization map. **Spike overlap = 0 today**
  — coverage is currently complementary, so the spread view has no live pairs yet
  but the plumbing is ready.
- **Volume units:** PM volume is USD, Kalshi is contracts. Rank within-source or
  normalize so selection isn't venue-biased.
- **Resolution:** PM is UMA-backed; treat only cleanly-resolved (`closed` + 0/1
  prices) as settled; expect occasional lag.
- **Join id:** use PM `conditionId` (stable) as `ticker`; `slug` for the URL.

## 8. Spike results (`python3 forecasting/spike_polymarket.py`)

- Discovered ~12 open company-KPI events → **29 unified rows**; shapes
  binary 7 / threshold 3 / bucket_cdf 2; 0 malformed.
- **Bucket→CDF proven:** MSFT Azure growth and Mastercard GDV growth converted
  to clean monotone ladders (MSFT median ~40.6).
- Real KPI ladders: Apple Greater China revenue (Q3, median ~$18.8B), MrBeast
  subscribers (~511M).
- Mention-market guard removes "earnings call" categorical events.
- Cross-source join runs; 0 overlapping `(company, period)` pairs right now.
- Writes `data/polymarket_kpi_open.jsonl` (new, additive) — nothing in the live
  pipeline touched.

## 9. Phasing

- **Spike** ✅ done — this document + `forecasting/sources/` + spike runner.
- **MVP (1–2 days):** `pull_all.py` merged output, `threshold`-field grouping +
  monotonicity clamp, PM settlement adapter, dashboard source badge + spread.
  End-to-end through `scripts/daily.sh`.
- **v2:** cross-venue spread strategy, direct per-bucket forecasting, mention /
  ranking market types as a separate track.
