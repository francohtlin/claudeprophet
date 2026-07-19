# ClaudeProphet

Claude-native forecasting harness for Prophet Hacks. When forecasting, read
`AGENTS.md` first — it holds the full operating instructions, timing policy, and
tool usage rules for this repo. Then follow the run prompt in
`prompts/goal_prompt.md`.

Key local tools (run via Bash from the repo root):

- `npm run market:lookup -- --text "..."`
- `npm run kalshi:discover -- --query "..." --status open`
- `npm run sports:lookup -- --query "..." --include-odds`
- `npm run finance:lookup -- --query "..." --symbols NVDA`
- `npm run submit:prediction -- --kind initial|final --event <event.json> --prediction <prediction.json>`
- `npm run metadata -- evidence|trace --workspace <dir> ...`

Native web research uses the built-in WebSearch tool. Return the final forecast
as strict JSON only.
