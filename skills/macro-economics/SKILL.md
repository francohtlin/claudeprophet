# Macro Economics

Use this skill for CPI, GDP, labor reports, housing starts, central bank decisions, Treasury yields, gas prices, and policy-rate questions.

## Evidence Sources

- FRED via `finance_lookup`
- official release calendars
- central bank statements
- consensus forecasts from reputable financial outlets
- market-implied rates/yields when available
- recent trend and revision history

## Calibration Notes

- Respect release timing and whether the market closes before the official release.
- For bucket outcomes, ensure buckets are mutually exclusive as encoded by the event.
- Do not treat cumulative threshold phrasing as mutually exclusive unless the event outcomes clearly require exactly one label.
- For cumulative threshold ladders, model the underlying released value first and derive monotone threshold probabilities from that latent distribution. Boundary thresholds near consensus need extra humility: one small unit/basis-point/cent miss can flip the row, so avoid >55-60% calls there without exact market or strong official/source evidence.
- For central-bank policy-rate decisions, treat broad futures-implied charts as weaker evidence unless they clearly state the exact meeting date, target-range outcome, timestamp, and whether prices are normalized. Prefer sources whose market/rules match the event directly. If two current rate-odds sources disagree materially, anchor on the better rule/date/outcome match and state the conflict rather than averaging blindly.
