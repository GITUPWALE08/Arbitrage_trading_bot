# Project Rules — Crypto Arbitrage Bot

Read `crypto_arbitrage_bot_spec_v2.md` in full before writing any code. These rules govern how you work on this project across all sessions. They exist because this bot will eventually touch real money — treat them as constraints, not suggestions.

## Non-negotiables

1. **Never hardcode API keys, secrets, or credentials in any file.** Use environment variables via `.env` (gitignored) and `python-dotenv`. If you find yourself about to type a real-looking key into source, stop.
2. **Never enable withdrawal permissions in any code path, example, or comment that suggests API key scope.** Trade-only, always. If a feature seems to need withdrawal access, flag it to the user instead of assuming it's fine.
3. **Live trading mode must be blocked by the Go-Live Gate (spec Section 9).** Do not build a way to bypass, skip, or short-circuit the gate — not even as a "just for testing" flag. If testing live order placement is genuinely needed, use exchange testnet/sandbox endpoints instead.
4. **Every execution attempt goes through the state machine (spec Section 2.1).** Do not write execution logic that places orders outside of it, even for "quick test scripts" — those scripts become load-bearing and skip reconciliation/recovery coverage.
5. **Paper trading and live trading must run the same strategy/execution/risk-manager code paths** (spec Section 2.5). Never fork logic between them. If you're tempted to special-case something for paper mode, that's a sign the abstraction is wrong.
6. **No silent failure.** Any error, partial fill, reconciliation discrepancy, or kill-switch trip must be logged AND alerted, per the relevant spec section. If you write a `try/except` that swallows an exception without logging it, that's a bug.
7. **Don't invent profit numbers, backtested returns, or performance claims.** If asked to project returns, say plainly that it depends on live market data you don't have, and point to paper-trading results once they exist.

## Build order

Follow spec Section 1 and Section 17: shared core (state machine → reconciliation → recovery → fee calculator → paper trading simulator) first. Do not start on Strategy A, B, or C logic until the core pieces in Section 2 are built and have basic tests. Do not build all three strategies in parallel — Strategy C first, alone, end to end, through paper trading, before starting Strategy A.

## Working style for this project

- **Work in small, reviewable increments.** One module or one section of the spec at a time, not the whole codebase in one pass. Stop and let the user review before moving to the next piece, especially for anything in Section 2 (core) or Section 9 (gate).
- **Write tests for the state machine, fee calculator, and reconciliation logic specifically** — these are the parts where a subtle bug is expensive. Standard test coverage elsewhere is fine but these three get priority.
- **When the spec is ambiguous or you have to make a judgment call** (e.g., exact thresholds, which exchange-specific quirk to handle), say so explicitly rather than silently picking one — these are risk parameters, not style choices.
- **Ask before adding dependencies** not listed in the spec's infra section (Section 7), especially anything that would touch order placement or account access.
- **If you notice a gap or inconsistency in the spec itself while building**, flag it to the user rather than quietly resolving it your own way — the spec is the source of truth and should be corrected, not silently overridden.

## Definition of done, per module

A module isn't done when it runs — it's done when:
- It has a test covering at least the main success path and one failure/edge case
- Errors are logged and, where relevant, alertable
- It's wired into the persistent state store where the spec calls for persistence (not just working in-memory)
- The user has reviewed it (for core/gate modules especially)

## What NOT to do

- Don't skip straight to a "working demo" that places real orders to prove the concept — paper mode first, always.
- Don't optimize for speed of delivery over the safety mechanisms in spec Sections 2, 6, and 9. Those sections exist because they're what makes this safe to eventually run with real capital — not overhead to trim if the build is taking a while.
- Don't write exchange integration code from memory for API details you're not certain about (endpoint paths, rate limits, fee schedules) — check current exchange API docs rather than guessing, since these change and being wrong here costs real money.
