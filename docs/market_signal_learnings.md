# Prophet Hacks Learnings For Claude Bot

This file is the short operational memory for a Claude-style forecasting bot. Use it as a probability-adjustment guide after the agent has found a relevant market price. Do not treat these as universal truths; each adjustment must map to a named market inefficiency.

## Prime Directive

Anchor on market probability when a relevant Kalshi/Polymarket-style market exists, then apply only structural corrections that have a reason:

- threshold-ladder monotonicity,
- high-overround sibling-row inconsistency,
- favorite-longshot bias,
- bid-ask adverse-selection asymmetry,
- crypto quote momentum,
- concrete-market underconfidence,
- topic/mention overconfidence.

Avoid generic curve fitting. Prefer event-held-out or group-k-fold-by-`event_ticker` evidence over row-level wins.

## Replication Update

See `docs/market_signal_replication.md` for the 2026-05-17 replication pass.

The larger 1.2k Kalshi-history backtest confirms category-gated sharpening, not global sharpening:

- `Other` 3-5 days before close is the cleanest positive signal. Category-only edge sharpening around 75% improved Brier from `0.052620` to `0.034368`; event-clustered bootstrap stayed positive.
- `Entertainment` is directionally positive with moderate 30-40% sharpening, but event-level evidence is less robust than `Other`.
- `Mentions` is weak/noisy. Light sharpening can help in rows, but do not trust it without corroborating evidence.
- Broad Sports, Companies, Economics, Politics, or all-market sharpening is not supported.
- Global p-conditional tail amplification is not robust on the 1.2k history backtest. It is row-positive in the 3-5 day window but negative under event-clustered weighting, so use it only as a weak diagnostic, not as a standalone production rule.

James's smaller dev-set repo signals still reproduce directionally for crypto quote momentum, bid-ask asymmetry, and topic-mention shrinkage, but the 1.2k candle cache lacks the historical bid/ask and previous-quote fields needed to independently validate those exact features at the same granularity.

## Integrate Now

### 1. Threshold-Ladder Monotonicity

Use for threshold rows such as:

```text
Above $100
Above $110
Above $120
```

These are nested events. Probabilities must be non-increasing as threshold rises. If market quotes violate this, project the full ladder onto the monotone decreasing cone with isotonic regression.

Useful add-on from James:

```text
After isotonic projection, apply slight implied-PDF smoothing with sigma = 0.5.
```

Evidence:

```text
microstructure_FL baseline: 0.046754
threshold PDF smoothing:   0.046645
OOF delta:                 +0.000109
```

Why this is believable: it is a logical constraint plus tiny tick-noise smoothing, not arbitrary category curve fitting.

### 2. Crypto Quote Momentum, Retuned Smaller

If current quote moved from previous quote in crypto threshold/price markets, direction has signal, but the old coefficient was too strong.

Use:

```text
crypto momentum coefficient = 0.05
```

Do not use the previous stronger `0.125` coefficient.

Evidence:

```text
baseline:      0.046754
retuned crypto: 0.046606
OOF delta:     +0.000148
```

All CV folds picked `0.05`, so this is small but stable.

### 3. Bid-Ask Adverse-Selection Asymmetry

When bid/ask are asymmetric relative to 0 and 1, the midpoint can be biased. If the bid is far from 0 and ask is close to 1, the book may be more comfortable on the YES side; if ask is far from 1 and bid is close to 0, the opposite.

Use only when:

```text
category not in {"Politics", "World", "Elections"}
spread >= about 0.03
```

Skip politics/world/elections because wide spreads there often mean news uncertainty, not market-maker adverse selection.

Evidence:

```text
baseline:                  0.046754
best adverse-selection BA: 0.046340
delta:                     +0.000413
```

This is useful but modest. Do not over-strengthen it.

### 4. p-Conditional Tail Amplification

James found that the real signal from the time-decay sweep was not time. It was that already-extreme probabilities were still too timid.

Use a gentle logit-scale amplification where the scale grows with distance from 0.5:

```text
shape = 4 * (p - 0.5)^2
scale = 1 + (amp - 1) * shape
p' = sigmoid(scale * logit(p))
```

Current implementation uses `amp = 2.0`.

Interpretation:

- no-op near 0.5,
- stronger push near 0 or 1,
- fits favorite-longshot / underconfident-tail behavior.

Do not call this a time-to-close alpha. The time-dependent versions did not beat the constant control.

Use with extra caution after the 1.2k replication pass: global p-conditional tail amplification was not event-robust on larger Kalshi-history data. It can still inform reasoning when another structural alpha points the same way, but it should not be applied blindly across all categories.

### 5. Category-Gated Sharpening For 3-5 Days Before Close

From our 1.2k Kalshi-history backtest, concrete `Other` and `Entertainment` markets often had market favorites that were directionally right but underconfident.

Practical rule:

```text
if 72h <= time_to_close <= 120h:
    if category == "Other" and event_is_concrete:
        sharpen toward 0/1 by 60-75%
    elif category == "Entertainment":
        sharpen toward 0/1 by 30-40%
    elif category == "Mentions":
        sharpen only lightly, 0-20%, and only with corroborating evidence
    else:
        do not category-sharpen
```

Safer combined policy:

```text
Entertainment + Other, edge 40%
base Brier: 0.109667
new Brier:  0.108217
delta:      +0.001450
```

Statistical caveat: row-level significance is strong, but event-clustered evidence is weaker for Entertainment. `Other` is the cleanest.

## Conditional / Thin Evidence

### Topic-Mention Shrinkage

Politics/topic mention markets may be overconfident. James found topic-mention rows with:

```text
actual YES rate: 0.379
mean base p:     0.540
```

OOF result:

```text
baseline: 0.046754
shrunk:   0.046457
delta:    +0.000297
```

But only 2 topic-mention events were detected. Use as a soft prior:

```text
For political speech/topic mention markets, avoid aggressive YES probabilities unless sources strongly support it.
Consider shrinking toward a low base rate.
```

### Multi-Winner Podium / Top-K Rows

For rows where K candidates can be YES, probability mass should sum to K, not 1. Example: podium/top-3 markets.

Structural idea:

```text
If row is Top-K and sum(probabilities) > K + tolerance:
    normalize row toward sum K, preserving favorite/longshot shape.
```

Evidence is too thin: only 1 multi-winner row in James's dev set. Keep as a detector/diagnostic, not a default production correction until more examples validate it.

### High-Overround Winner Row Power

For mutually exclusive winner rows with large overround, row probabilities should sum to 1. Power normalization helps by compressing longshots while preserving obvious favorites.

But James found the strongest power settings were partly driven by only 4 activated events and one data artifact where the true winner was missing from listed candidates.

Use conservative structural normalization. Avoid blindly cranking power to 15-20.

## Negative Results / Do Not Integrate Blindly

### Do Not Use Shin Overround Correction

Shin overround lost to the existing power=2 row normalization in OOF CV.

```text
current power=2: 0.046754
Shin model:      0.047338
```

Do not integrate.

### Do Not Use Spread-Based Shrinkage Toward 0.5

Continuous spread-aware shrinkage selected no-op in every fold. Wide spread did not mean uncertainty after favorite-longshot correction; it was confounded with category and longshot structure.

Do not shrink toward 0.5 just because spread is wide.

### Do Not Trust Per-Category Isotonic / Logit Calibration Yet

Per-category calibration looked good in-fold but overfit out-of-fold. Only use category calibration when there is a named structural reason and enough event-level support.

### Do Not Globally Sharpen Sports

Sports has many rows but no stable broad sharpening alpha. Any sports edge likely needs external data:

- injury/news lookup,
- odds consensus,
- line movement,
- lineup/player availability,
- rest/travel/schedule.

## Implementation Ordering

When predicting from market data:

1. Parse raw market probability.
2. If row siblings exist, detect row type:
   - threshold ladder: isotonic + sigma=0.5 PDF smoothing,
   - mutually exclusive winner row: conservative overround normalization,
   - multi-winner Top-K: diagnostic only unless validated.
3. Apply crypto momentum only for crypto quote-delta cases with coefficient `0.05`.
4. Apply bid-ask adverse-selection asymmetry only outside politics/world/elections.
5. Apply gentle tail amplification.
6. Apply category/time gated sharpening only for 3-5 day `Entertainment`/concrete `Other` cases.
7. Avoid confident overrides unless external source tools confirm a non-market edge.

## Bias Warnings

- Row-level tests overcount correlated examples from the same event.
- Multi-outcome rows produce dependent binary labels.
- Some discoveries are from the 100-row dev set, not the 1.2k backtest.
- Some category rules were selected after inspecting the data.
- Event-held-out CV is better than row CV, but similar market families can still leak across folds.
- Calibration can drift across time, liquidity regimes, and competition task mix.

Use this file to guide the bot's priors, not as a substitute for live evidence.
