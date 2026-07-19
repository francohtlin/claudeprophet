# Finance And Crypto Forecasting

Use this skill for equities, crypto, rates, commodities, earnings, CPI-style releases, and market-threshold questions.

## Commands

```bash
npm run finance:lookup -- \
  --query "<event question>" \
  --symbols "<comma-separated tickers>" \
  --asset-type auto \
  --data-needed price,history,news,macro,filings \
  --lookback-days 30 \
  --max-items 8
```

Use web search for:

- official company investor relations
- SEC filings and earnings calendars
- official macro release calendars
- central bank statements
- exchange-specific crypto price definitions when resolution rules name an exchange

## Calibration Notes

- For threshold events, estimate distance to threshold, time remaining, realized volatility, and event catalysts.
- Use the resolution source named in the rules, not a convenient alternate source.
- If a market references a specific exchange/candle, other exchanges are only proxy evidence.

