Forecast the event below as accurately as possible for Prophet Hacks.

Before forecasting, read local instructions according to the available budget:

1. `AGENTS.md`
2. `docs/context_memory.md`
3. `docs/market_signal_learnings.md`
4. `skills/calibration-validation/SKILL.md`

If `INTERNAL_TIME_BUDGET_SECONDS` is under 120, use short-budget mode:
- Read only the timing/tool sections of `AGENTS.md` plus any directly relevant skill snippets.
- Do not spend time reading long background docs before evidence gathering.
- Within 45 seconds, do one high-signal external evidence pass, write a calibrated `initial` checkpoint, and overwrite it whenever evidence improves.
- Prefer one native web search plus one matching local tool call over broad exploration.
- Short-budget mode must still return a final forecast. Save `--kind final` and return strict JSON at least 10 seconds before `INTERNAL_TIME_BUDGET_SECONDS`; do not keep researching until the API kill switch.
- For central-bank or rate-decision events, use exact meeting/date and exact outcome terms in the search. Do not let one broad futures chart override better-matched live prediction-market or rate-decision odds; if quoted sources disagree by more than 10 percentage points, mention the conflict and anchor on the source whose rules/date/outcome best match the event.

Treat `docs/market_signal_learnings.md` as conditional alpha guidance. Apply an alpha only when the event and any discovered market data match the named condition; do not blindly curve-fit every forecast.

Then use any other relevant skill files under `skills/`.

Time budget:
- You have `INTERNAL_TIME_BUDGET_SECONDS` seconds inside a real evaluator timeout of 600 seconds. Use the available budget when more research is likely to improve the forecast.
- Treat research depth as part of correctness. A fast forecast that ignores plausible recent non-market updates is a failed forecast even if the JSON schema is valid.
- Depth does not mean contrarianism. If an exact matching prediction market exists with recent prices and nontrivial liquidity, treat it as a strong Bayesian prior. Deviate only when you can name specific evidence the market is likely underweighting, explain why it is not already priced, and quantify the adjustment.
- Before external research, form and save a valid initial forecast from the event JSON and embedded market data.
- If the budget is under 120 seconds, save a researched initial checkpoint within 45 seconds. Otherwise, save an evidence-backed checkpoint within 60-90 seconds.
- Keep overwriting the initial checkpoint as your best current forecast improves.
- For a normal 10-minute request, do not stop at the first valid researched forecast. After checkpointing, keep using the budget for higher-value evidence until the main uncertainty is genuinely narrowed or the evaluator deadline is becoming risky.
- A nontrivial live/current forecast should usually use roughly 40-60 focused external/search/tool calls and log at least 6-12 distinct evidence items when sources are available. For normal 10-minute live events this is a hard research-depth requirement unless the event is logically resolved or sources are unavailable after documented separate attempts. Do not count one broad search as many checks.
- Scale research into explicit loops. For a normal 10-minute live/current event, run at least 6 research loops before any final checkpoint. Each loop should contain about 8-10 focused external/search/tool checks, for roughly 50 checks total. After each loop, write/update an `initial` checkpoint and explain the Bayesian update from the previous loop. Only the final loop may produce `--kind final`.
- Loop traces are mandatory and must be separate. After each loop, run `.venv/bin/python -m api_service.run_metadata trace --workspace "$REQUEST_WORKSPACE" --stage research_loop_<n>_complete --message "loop=<n>; new_checks=<n>; cumulative_checks=<n>; bayesian_update=<summary>"` using the concrete loop number, through at least `research_loop_6_complete` for normal 10-minute live/current events. Do not claim `loops=6` in the final audit unless all six loop trace stages exist. Do not collapse loops 1-6 into one trace or one broad provider/search pass.
- Scale the loop count with budget: under 120 seconds uses short-budget mode; 120-300 seconds requires at least 2 loops and 10-15 checks total; 300+ seconds requires at least 6 loops and 40-60 checks total unless a valid exception applies.
- If you are about to finalize before 40 focused external/search/tool calls on a normal 10-minute live/current event, stop and justify it. "The answer seems obvious", "the market is confident", and "one strong source agrees" are not valid justifications. Valid justifications are logical resolution, unavailable sources after separate documented attempts, or fewer than about 90 seconds remaining.
- Proceed from high-signal sources down to lower-signal sources as time passes: source-of-resolution/official source, matching markets or structured data, recent non-market updates, independent corroboration/base rates, then broad low-signal searches only if still useful.
- Update Bayesianly in batches. Roughly every 10 tool calls/evidence checks, rewrite/checkpoint the current best forecast and adjust probabilities based on what the new evidence changed, did not change, and left uncertain.
- For normal nontrivial live/current events, do not write the `final` checkpoint after only the first evidence batch. Use `--kind initial` for interim Bayesian updates after each batch. Write `--kind final` only after roughly 40 focused external/search/tool calls and at least 6 separate research-loop traces, after the high-signal ladder and required domain-specific freshness sweeps are exhausted, or when fewer than about 90 seconds remain. Three evidence items or three quick batches alone is not enough for a normal 10-minute live event. Exceptions: trivial/self-test events, logically resolved events, or unavailable sources after documented attempts.
- For AI model leaderboard events, separately check the exact leaderboard/source-of-resolution, matching market page, and recent official launches/availability changes from plausible providers such as Google/Gemini, OpenAI/ChatGPT, Anthropic/Claude, xAI/Grok, DeepSeek, Qwen, Kimi, GLM, and MiniMax. Use one provider-specific official-news query per plausible provider or outcome family; do not combine the provider sweep into one broad query. A strong market favorite does not excuse skipping this sweep.
- For AI model leaderboard events, before finalizing verify that you checked: exact resolution leaderboard, exact matching market or documented failed exact-market lookup, independent leaderboard/source snapshot, separate official-news/release checks for Google/Gemini, OpenAI/ChatGPT, Anthropic/Claude, xAI/Grok, Qwen, Kimi, GLM, and MiniMax when plausible, and a Bayesian update explaining how new launch evidence changed the forecast. If any item is missing and more than 90 seconds remain, do not finalize.
- For AI leaderboard events, do not turn "new model launched but not reflected yet" into a large upset probability by itself. Anchor on the exact market and current leaderboard unless the new model is already live in the resolution arena, has official benchmark evidence that directly maps to the resolution leaderboard, or has market movement showing traders are repricing it.
- If an exact matching market is found, before finalizing any outcome more than about 2x above or below its market-implied probability, write an explicit adjustment note: market probability, final probability, new evidence, why the market is stale/wrong, and why the adjustment size is justified. Without that note, stay close to the market.
- If the event is a Kalshi `KX*` event and `evidence_manifest.json` contains a `prediction_market_snapshot` item, treat its `kalshi_event_snapshot.json` as the frozen request-time exact market baseline. Read/use that snapshot before generic market search. It includes `mutually_exclusive`, each listed market, bid/ask/mid, status, and result when visible.
- For cumulative macro threshold ladders such as jobless claims, gas prices, CPI/PCE, GDP, housing starts, TSA traffic, or similar "above / at least / greater than" outcomes, forecast the latent continuous value first, then convert it into monotone threshold probabilities. Do not forecast each row independently. Around a consensus or source boundary, keep uncertainty wide enough that the boundary threshold is not casually pushed above 55-60% unless exact market prices, official-source evidence, or a well-justified distributional model supports it. If the exact market midpoint is near even money, a small miss of one unit/basis point/barrel cent can flip the threshold, so avoid overconfident boundary calls.
- Before every final checkpoint, run an explicit finalization self-audit trace command. This is mandatory, not optional prose: `.venv/bin/python -m api_service.run_metadata trace --workspace "$REQUEST_WORKSPACE" --stage finalization_self_audit --message "loops=<n>; evidence_count=<n>; external_tool_calls=<n>; required_sweeps=<done/missing>; skipped=<none/reasons>; market_anchor=<yes/no>; verdict=<pass/fail>"`. The verdict is `fail` if external_tool_calls is below 40 without a valid exception, if fewer than 6 separate loop trace stages were written on a normal 10-minute live event, if a required domain-specific sweep is missing, if market anchoring was skipped for an exact market, or if the Bayesian update is not documented. If the verdict is `fail`, do not write `--kind final`; write/update `--kind initial` and keep researching.
- When final research/calibration is done, save `npm run submit:prediction -- --kind final --event EVENT_FILE_OR_TASK --prediction <prediction.json>` and return the same JSON.
- After every tool call, reassess remaining time. If time is running low, stop researching and return the best valid JSON immediately.
- If fewer than 15 seconds remain in `INTERNAL_TIME_BUDGET_SECONDS`, stop all research, write/validate `--kind final` from the best available checkpoint/evidence, and return it immediately.
- Do not stop early just to be fast; stop early only when the marginal lookup is unlikely to change the forecast or the evaluator deadline is becoming risky.

Request metadata:
- `REQUEST_WORKSPACE` is the directory for this run when available.
- `EVIDENCE_MANIFEST` points to the structured evidence manifest. Read it before repeating expensive lookups. If you use important evidence from native web search or another source that is not automatically logged by a local tool, append a concise item with `.venv/bin/python -m api_service.run_metadata evidence --workspace "$REQUEST_WORKSPACE" --kind ... --source ... --query ... --notes ...`.
- `TRACE_LOG` points to the run timeline. You may append major agent-side milestones with `.venv/bin/python -m api_service.run_metadata trace --workspace "$REQUEST_WORKSPACE" --stage ... --message ...`, but do not let tracing delay the final answer.
- `ACTIVE_VARIANT_ID` and `ACTIVE_VARIANT_JSON` identify the forecast strategy version for this run.

Iterate:
1. Parse the event and exact outcomes.
2. Gather market, sports, finance, news, official-source, and base-rate evidence as relevant. Start with the highest-signal exact-match sources, then deepen with alternate phrasings, plausible upset outcomes, independent corroboration, and base-rate checks while budget remains.
3. Decide whether outcomes are mutually exclusive or whether multiple outcomes can be correct. For Top-K / winning-set events, output inclusion probabilities that may sum to K; do not normalize them to 1.
4. Validate/checkpoint JSON with `npm run submit:prediction --` when event JSON is available.
5. Revise until the forecast is valid.

Return final JSON only unless the user explicitly asks for explanation outside the JSON.

EVENT_OR_TASK:
