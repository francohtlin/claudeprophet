# Context Memory For ClaudeProphet

Durable project context:

- The project should be completely separate from `pi-prophet-hanson`.
- Do not use symlinks back to another repo.
- Do not Dockerize this path by default.
- The primary forecasting engine is Claude (Claude Code) launched with a local Claude subscription session via `claude login` (or a `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`). `ANTHROPIC_API_KEY` is intentionally disabled for forecasts (subscription only, no API key).
- Default model should be `claude-opus-4-8`.
- Claude's built-in WebSearch tool should be available for Claude launches (no flag needed).
- The repo should also serve a local API for internal testing on this machine.
- Public exposure should be a separate step through a tunnel or deployment URL, not the default local mode.
- Market search returns raw market data. The agent performs inference and calibration.
- If a matching prediction market does not exist, use nearby markets, sports/finance/news/official data, and base rates.
- Market-signal calibration rules live in `docs/market_signal_learnings.md`; use them as named-inefficiency priors, not as hard-coded truth.
- Avoid overfitting to sample datasets; they are validation and smoke-test fixtures.
- Validate final forecasts before returning them.
