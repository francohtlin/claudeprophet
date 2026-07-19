# Sports Forecasting

Use this skill for games, series, standings, playoffs, awards, injuries, and betting-market questions.

## Commands

```bash
npm run sports:lookup -- \
  --query "<event question>" \
  --sport auto \
  --days 14 \
  --max-events 10 \
  --include-odds
```

Use web search for:

- injury reports
- starting lineups
- rest/travel context
- official schedules
- playoff format and tiebreakers
- award voting timing and odds

## Calibration Notes

- Book odds are strong short-horizon evidence for games.
- Injuries and confirmed starters can dominate prior strength.
- For series/season outcomes, use current standings, remaining schedule, market odds, and bracket path.

