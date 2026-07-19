# Market Discovery

Use this skill when an event might have a matching or nearby prediction market.

## Commands

Concise agent-facing market lookup:

```bash
npm run market:lookup -- \
  --text "<event question and rules>" \
  --category "<category>" \
  --ideal-close-time "<ISO close time if known>" \
  --max-markets 10
```

Kalshi market history, when price movement matters:

```bash
npm run market:lookup -- \
  --text "<event question and rules>" \
  --max-markets 10 \
  --include-history \
  --history-lookback-days 7 \
  --history-trade-limit 50 \
  --history-candle-limit 48
```

Exhaustive Kalshi discovery:

```bash
npm run kalshi:discover -- \
  --query "<search query>" \
  --status open \
  --max-pages 5 \
  --max-markets 100
```

## Interpretation

- Use market prices as strong evidence only if the market's question, rules, outcomes, and timing match the Prophet event.
- Treat Kalshi history as raw evidence about price movement, trade prints, volume, and open interest; it is not a score or forecast.
- Discount low-liquidity, stale, duplicate, or broad proxy markets.
- Do not expose hidden URLs/IDs in final output unless asked; the forecast should cite the market evidence conceptually and concisely. History output omits Kalshi tickers and trade IDs by default.
