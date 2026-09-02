# Build Spec Addendum — Mode Switching (Demo / Testnet / Live)
**Version 1.0 | Companion to crypto_arbitrage_bot_spec_v2.md — hand this to your coding agent alongside the main spec and CLAUDE.md**

## 0. Context and Constraint

The bot already supports three execution modes via `main.py` branches: `simulated` (fake fills against live market data, via `SimulatedExchangeClient`), `testnet` (real orders on an isolated sandbox, via `CCXTExchangeClient` with `testnet=True`), and now `demo` (real orders against real-time prices with virtual balance, via `CCXTExchangeClient` pointed at the exchange's demo API base URL — e.g. Bybit's `api-demo.bybit.com`).

This document specifies how mode switching moves from "set once at startup via env var" to "controllable at runtime via Telegram, with live capital protections."

**Do not implement this as a hot-swap.** Mode switching is a controlled restart, not a live in-process client swap. Read Section 1 before writing any code — this constraint shapes the whole design.

---

## 1. Architecture: Restart-Based Switching

1. A mode-switch command writes a `pending_mode` value to the persistent state store (not just Redis — must survive a process restart).
2. The bot finishes any in-flight execution (lets the state machine reach a terminal state — `COMPLETED`, `UNWOUND`, or `STUCK` — per spec Section 2.1) before proceeding. Do not interrupt an execution mid-flight to switch modes.
3. Once safe, the bot triggers its own process exit with a specific exit code (or writes a flag file `systemd`/`supervisord` is configured to check) to initiate a controlled restart.
4. On startup, before anything else, the bot reads `pending_mode` from persistent state (if set), applies it as the active mode, clears the pending flag, and then runs the **existing crash-recovery flow** (spec Section 2.3) as normal — recovery logic doesn't need to know or care that this restart was a deliberate mode switch rather than a crash; treat it identically.
5. This means: no new recovery logic is needed for mode switching specifically. It reuses Section 2.3 entirely. Confirm this reuse explicitly rather than writing a parallel recovery path — a second recovery implementation is a maintenance and correctness risk.

## 2. Kill Switch Reuse for Pausing

Before triggering the restart in Step 3 above, the bot should trip the relevant scope's kill switch (global, if switching globally — see spec Section 6.2) so no *new* opportunities are evaluated while the switch is pending, while still allowing the current in-flight execution to reach a terminal state. This reuses the existing kill-switch mechanism rather than inventing a separate "pause for mode switch" state.

## 3. Telegram Commands to Add

Add these to the Telegram command set (extends the list already built out):

```
/mode                       Show current active mode, when it was last set, and by whom
/switch_demo                 Low-friction switch to demo mode
/switch_testnet               Low-friction switch to testnet mode
/switch_live                  Step 1 of live switch (see Section 5 below)
/confirm_live <code>          Step 2 of live switch (see Section 5 below)
```

Query commands already built (`/status`, `/positions`, `/balance`, `/pnl`, `/pnl_detail`, `/opportunities`, `/executions`, `/gate_status`) all need to be updated per Section 6 (data scoping) — see below, this is not a new command, it's a behavior change to existing ones.

Add mode-suffixed variants for cross-mode viewing without switching:
```
/pnl_demo   /pnl_testnet   /pnl_live
/positions_demo   /positions_testnet   /positions_live
/balance_demo   /balance_testnet   /balance_live
```
(Apply the same `_demo`/`_testnet`/`_live` suffix pattern to any other query command where cross-mode comparison would be useful — use judgment, but the three above are the minimum.)

## 4. Low-Friction Switches: `/switch_demo`, `/switch_testnet`

Before proceeding, check for open positions in the mode **being left**:
- Query `/positions` scoped to the current active mode.
- If any open positions exist: **block the switch** and reply with the list of open positions and why the switch is blocked (leaving this mode means the position stops being monitored — no more reconciliation, no more margin/liquidation checks for Strategy C, no more kill-switch coverage).
- Offer an explicit override: `/switch_demo force` (or equivalent) — but this override must display a clear warning about the monitoring gap and require it to be typed deliberately, not just a button tap, since it's the kind of thing a stray tap could trigger otherwise.

If no open positions (or override confirmed): proceed with the restart flow from Section 1.

## 5. High-Friction Switch: `/switch_live`

This must be materially harder to trigger than the demo/testnet switches. Two-step process:

**Step 1 — `/switch_live`:**
1. Run the **exact same `go_live_gate.py`** logic used to block live startup (spec Section 9) — not a separate or looser check. If any gate criterion fails, reply with the failure report and stop here. Do not proceed to step 2 on a failed gate.
2. If the gate passes, check for open positions in the mode currently being left, same as Section 4.
3. If clear, generate a short random confirmation code, store it with a short expiry (e.g., 5 minutes) in the state store, and reply with: the passed gate report, a clear warning that this will engage real capital, and instructions to send `/confirm_live <code>` within the expiry window.

**Step 2 — `/confirm_live <code>`:**
1. Check the code matches and hasn't expired. If not, reject and require starting over from `/switch_live` — do not allow retries against the same code, and do not extend expiry.
2. If valid, proceed with the restart flow from Section 1, targeting live mode.

**Do not build any path that skips step 1's gate check, including for testing purposes.** If a "test the switch mechanism without the full gate" need comes up during development, test it against `testnet` or `demo` mode switching instead — the live path should have no code-level bypass, ever, including behind a debug flag.

## 6. Data Scoping

1. Add a `mode` column (`simulated` / `testnet` / `demo` / `live`) to every table in the spec's data model (Section 10) that records mode-relevant activity: `executions`, `execution_legs`, `opportunities`, `balances_snapshot`, `reconciliation_log`, `system_events`, `margin_monitoring`. This is an additive schema change — write a migration, don't alter existing row semantics.
2. Every write path (execution engine, reconciliation, logger) must stamp the row with the mode active **at the time of the write**, not looked up later — this matters because mode can change between when data was recorded and when it's queried.
3. Every query command's default behavior: filter results to the **currently active mode** at query time. `/pnl` while the bot is in demo mode shows demo P&L only; after a switch to live, the same `/pnl` command shows live P&L only.
4. Mode-suffixed commands (Section 3) bypass the active-mode filter and query a specific mode explicitly, regardless of what's currently active — this is how cross-mode comparison happens without needing to switch.

## 7. Audit Logging

Every mode-switch attempt — successful, blocked by the gate, blocked by open positions, expired/invalid confirmation code, or the low-friction override path — is written to `system_events` (existing table) with: who triggered it (should always be the whitelisted Telegram user, per existing auth rules), what mode was requested, what mode was active, outcome, and reason if blocked. This is the highest-stakes command surface in the bot; its audit trail should be the most complete, not an afterthought.

## 8. Explicit Non-Goals

- No hot-swapping of exchange clients mid-process. If this ever becomes a real need later (e.g., for latency reasons), that's a significant separate design conversation, not an incremental addition to this one.
- No live-mode gate bypass under any flag, env var, or debug mode.
- No mode switch while an execution is genuinely mid-flight (not yet at a terminal state) — the switch waits, it does not interrupt.

## 9. Definition of Done

- `/mode`, `/switch_demo`, `/switch_testnet`, `/switch_live`, `/confirm_live` all implemented and functioning per above
- Attempting `/switch_live` with a failing Go-Live Gate is blocked and shows the failure report, verified with a deliberately-failing test scenario
- Attempting to switch away from a mode with an open position is blocked, verified with a test position left open
- A full switch (demo → testnet → back to demo) has been run manually at least once, confirming: in-flight execution completes first, restart occurs, crash-recovery flow runs and reports cleanly, subsequent queries reflect the new mode's data only
- `mode` column present and correctly populated on all listed tables, confirmed via direct DB query after a mode switch
- All mode-switch attempts (success and failure) appear in `system_events`
