# Market Signal Replication Notes

Date: 2026-05-17

Purpose: reproduce James Gui's market-signal hypotheses where possible, then check which ones survive a larger Kalshi-history backtest before adding them to the agent's operational learnings.

## Source Material

- James repo checked locally at `/Users/hansonwen/prophethacks/prophet-hacks`, remote `https://github.com/james-gui/prophet-hacks`, commit `f61293f`.
- James dev set: `data/prophet_arena_subset_100/subset_data_100.csv`.
- Larger Kalshi-history backtest: `results/kalshi_history_backtest/kalshi_history_backtest_predictions.csv`.
- Larger backtest size: 46,528 base prediction rows, 739 events, 4,213 tickers, offsets from 48h to 336h before close.

## What Reproduced On James's Dev Set

These reruns reproduced the direction of James's claims on the 100-event dev set:

| Signal | Rerun Result | Read |
|---|---:|---|
| Crypto quote momentum, coefficient 0.05 | OOF Brier 0.046599 vs baseline 0.046659 | Positive, small, stable on dev |
| Bid-ask asymmetry | Best BA variants around 0.046259-0.046282 vs 0.046659 | Positive on dev; politics/world regress |
| Topic-mention shrinkage | OOF Brier 0.046362 vs 0.046659 | Positive but only 2 events |
| Time/tail amplification | Best time-dependent OOF 0.046104; constant p-conditional control 0.046163 | Tail/extreme sharpening exists, true time signal is weak |

Some exact values differ slightly from older `RESULTS.md` numbers because the current repo commit has a slightly different baseline stack. The conclusions did not flip.

## What The 1.2k Kalshi-History Backtest Confirms

The larger cache is strongest for testing probability sharpening from actual Kalshi price history 2-14 days before close. It does not contain full historical bid/ask books for every offset, so it cannot independently re-test bid-ask asymmetry or previous-quote momentum at the same feature granularity as the James dev scripts.

### Global Sharpening Is Not A Robust Alpha

All offsets, all categories:

| Strategy | Brier | Delta vs Base |
|---|---:|---:|
| Base market probability | 0.117039 | 0.000000 |
| p-conditional tail amp 1.25 | 0.116856 | +0.000182 |
| p-conditional tail amp 1.50 | 0.116995 | +0.000044 |
| edge sharpening 0.10 | 0.117399 | -0.000361 |

At 3-5 days before close, all-category tail amplification is row-positive but not event-robust:

| Strategy | Row-Weighted Delta | Event-Mean Delta | Event Bootstrap Read |
|---|---:|---:|---|
| p-conditional tail amp 2.0 | +0.000228 | -0.000981 | reject as global rule |
| edge sharpening 0.10 | +0.000373 | not used | too weak and not stable |

Conclusion: do not apply global confidence sharpening. It is dominated by category/family effects and can reverse under event clustering.

### Category-Gated Sharpening Is The Usable Signal

3-5 day window: offsets 72h, 96h, 120h.

| Category | Rows | Events | Base Brier | Best Edge Nudge | New Brier | Delta |
|---|---:|---:|---:|---:|---:|---:|
| Other | 658 | 27 | 0.052620 | 75% | 0.034368 | +0.018251 |
| Entertainment | 2,338 | 65 | 0.066184 | 30% | 0.060850 | +0.005333 |
| Mentions | 838 | 13 | 0.164397 | 10% | 0.162530 | +0.001867 |
| Sports | 7,725 | 468 | 0.116866 | none | n/a | no robust broad edge |
| Companies | 888 | 18 | 0.143554 | none | n/a | broad sharpening hurts |
| Economics | 393 | 14 | 0.067463 | none | n/a | broad sharpening hurts |

Practical all-row gated policies over the 3-5 day window:

| Policy | Brier | Delta vs Base |
|---|---:|---:|
| Entertainment + Other, edge 40% | 0.108217 | +0.001450 |
| Entertainment + Other + Mentions, edge 30% | 0.108323 | +0.001343 |
| Other only, edge 75% | 0.108888 | +0.000779 |
| Entertainment only, edge 40% | 0.108866 | +0.000801 |

Event-clustered bootstrap is more conservative:

| Policy | Event-Mean Delta | 95% Bootstrap CI | Read |
|---|---:|---|---|
| Other only, edge 75% | +0.000763 | [+0.000161, +0.001511] | robust positive |
| Entertainment + Other + Mentions, edge 30% | +0.000914 | [+0.000121, +0.001671] | positive, but Mentions is noisy |
| Entertainment + Other, edge 40% | +0.000907 | [-0.000130, +0.001873] | positive but borderline |
| Entertainment only, edge 40% | +0.000282 | [-0.000605, +0.001126] | not event-robust alone |

## Agent Policy Update

Use these as priors, not hard-coded truth:

1. Anchor on relevant market probability.
2. If 72h-120h before close and category is `Other` with concrete resolution rules, sharpen strongly toward 0/1, around 60-75%.
3. If 72h-120h before close and category is `Entertainment`, sharpen moderately, around 30-40%, preferably with external corroboration.
4. If category is `Mentions`, only sharpen lightly when other evidence agrees.
5. Do not globally sharpen Sports, Companies, Economics, Politics, or all markets.
6. Do not use p-conditional/global tail amplification as a standalone production rule based on the 1.2k history backtest.

## Remaining Gaps

- The larger history file has sampled candle probabilities, but not full historical bid/ask/orderbook fields. Bid-ask asymmetry needs a separate data pull if we want a true 1.2k replication.
- Crypto quote momentum on James's dev data uses current-vs-previous quote fields. The candle backtest can test time-series price momentum, but that is a related feature, not the exact same feature.
- Topic-mention shrinkage is plausible but thin. The 1.2k category-level `Mentions` result is weak and noisy.
- Event families can leak across event-held-out folds. Treat all alphas as calibration priors, not independent proof.
