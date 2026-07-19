# Calibration And Validation

Use this skill for every forecast.

## Checklist

- Confirm every outcome label exactly matches the event's `outcomes`.
- Multi-outcome probabilities must include all outcomes exactly once.
- If exactly one outcome can be correct, probability mass should usually sum
  near 1.0 after normalization.
- If multiple outcomes can be correct, such as Top-K or winning-set events,
  return per-outcome inclusion probabilities. These may sum to K or another
  value above 1; do not normalize them into a single-winner distribution.
- Avoid 0 and 1 unless the event is already logically resolved.
- Prefer calibrated intervals over overconfident point guesses.
- If prediction-market data is available, compare it to your evidence rather than blindly copying it.
- For thin evidence, use a base-rate prior and widen uncertainty.

## Validation Command

```bash
npm run submit:prediction -- --event tmp/event.json --prediction tmp/prediction.json --require-probability-sum
```

If no event file exists, create one from the provided event JSON in `tmp/event.json`.
