# Crypto Arbitrage Bot — Technical Specification
**Version 2.0 | For handoff to a coding agent / developer**

Changelog from v1.0: added execution state machine, reconciliation, crash recovery, comprehensive fee-aware profit calc, realistic paper trading, proper partial-fill handling, reworked funding-rate logic, kill switches, a Go-Live Gate, a full data model, and a set of P1/P2 hardening items. This version is meaningfully more work to build than v1.0 — that's intentional. These are the pieces that determine whether the bot is safe to run with real money, not nice-to-haves.

---

## 0. Read This First (Non-Negotiable Context)

This document specifies three arbitrage strategies, ranked by feasibility for a solo builder. It is written to be handed directly to a coding agent (Claude Code, Cursor, etc.) as a build spec.

**Important framing before anything else:**
- No specific profit number or timeline (e.g., "$2k in 2 months") can be engineered into a spec. Profitability depends on capital size, market volatility, fee tiers, and execution quality — none of which are fixed until you run it.
- Every strategy below **must** ship with a paper-trading (simulation) mode first, and must pass the **Go-Live Gate** (Section 9) before touching real capital.
- This is not financial advice. You are responsible for your own capital, tax reporting, and compliance with your local regulations and each exchange's terms of service (some exchanges restrict or ban automated trading on retail accounts — check ToS).

---

## 1. Strategy Comparison (Build Priority)

| Strategy | Capital needed to start | Technical difficulty | Realistic edge size | Competition level |
|---|---|---|---|---|
| **A. Triangular Arbitrage** (single exchange) | Low ($200–500+) | Medium | Small, frequent | High (bots), but exchange-internal so more accessible |
| **B. Cross-Exchange Arbitrage** | Medium–High (capital split across 2+ exchanges) | Medium–High | Medium, less frequent | High, plus transfer-time risk |
| **C. Funding Rate ("Cash and Carry") Arbitrage** | Medium ($500+, more capital = more $ profit) | Low–Medium | Small % but very consistent | Medium |

**Recommended build order: C → A → B.** Build shared core first (Section 2) — it's now the majority of the engineering effort and everything else sits on top of it.

---

## 2. Shared Architecture (All Three Strategies Use This Core)

```
/arb-bot
  /core
    exchange_client.py        # unified wrapper per exchange (REST + WebSocket)
    orderbook_manager.py      # maintains live in-memory order books, staleness checks
    fee_calculator.py         # comprehensive net-profit math
    risk_manager.py           # position limits, kill switches, circuit breakers
    execution_engine.py       # state machine, order placement, partial-fill handling
    reconciliation.py         # compares internal state vs actual exchange state
    recovery.py                # crash detection + safe resume on restart
    state_store.py             # persistent state (DB) + fast state (Redis)
    latency_monitor.py         # tracks decision-to-fill timing
    logger.py                  # structured logging + trade journal
    notifier.py                 # Telegram/Discord/email alerts
  /strategies
    triangular.py
    cross_exchange.py
    funding_rate.py
  /backtest
    replay_engine.py
  /paper_trading
    simulator.py                 # realistic fill simulation (Section 2.4)
  /gate
    go_live_gate.py              # automated pre-live checklist (Section 9)
  config.yaml
  main.py
  requirements.txt
```

### 2.1 Execution State Machine

Every arbitrage attempt — regardless of strategy — moves through an explicit, persisted state machine. Do not let execution logic be implicit in a script's control flow; every transition must be written to the state store (Section 10) **before** the next action is taken, so a crash mid-execution leaves a recoverable trail rather than an unknown position.

**States:**
```
IDLE
  → OPPORTUNITY_DETECTED   (spread found, not yet validated)
  → VALIDATING             (re-check order book depth, fees, staleness at decision time)
  → EXECUTING_LEG_1
  → EXECUTING_LEG_2
  → EXECUTING_LEG_3        (triangular only)
  → CONFIRMING_FILLS       (poll/await fill confirmation on all legs)
  → RECONCILING            (compare expected vs actual fills, balances)
  → COMPLETED               (success, fully reconciled)
  → PARTIAL_FAILURE        (one or more legs failed or partially filled)
  → UNWINDING               (actively closing out unwanted exposure)
  → UNWOUND                 (unwind succeeded, exposure flat again)
  → STUCK                   (unwind failed or exposure could not be closed — requires alert + manual intervention)
  → FAILED                  (no legs executed, opportunity abandoned cleanly)
```

**Rules:**
- Every state transition is written to persistent storage synchronously before the next action executes.
- `STUCK` is a first-class state, not an exception path — it means the bot has residual directional exposure it could not automatically resolve. This must trigger an immediate high-priority alert (not just a log line) and halt further trading on that instrument/exchange until manually cleared.
- A single execution attempt (one full pass through the state machine) is the atomic unit for reconciliation and logging — one row per attempt in the `executions` table (Section 10).

### 2.2 Reconciliation

A separate, continuously-running process (not just something that fires after each trade) that:
- Periodically (e.g., every 60s, and always after any execution completes) fetches actual balances/positions from each exchange via REST
- Compares them against the bot's internally tracked state (what it *believes* it holds, based on confirmed fills)
- Flags and alerts on any discrepancy beyond a small tolerance (dust-level rounding is expected; anything larger indicates a missed fill, a fill the bot didn't record, or an external action on the account)
- Writes discrepancies to a `reconciliation_log` table with severity, and auto-halts trading on that exchange if discrepancy exceeds a configurable threshold
- This is the bot's ground truth check — internal state is a belief, exchange balance is the fact, and reconciliation is what keeps them from silently diverging

### 2.3 Crash / Recovery

- On every startup, before resuming any trading, the bot must:
  1. Read the last known state from persistent storage for every in-flight execution
  2. For any execution not in a terminal state (`COMPLETED`, `FAILED`, `UNWOUND`), treat it as a **recovery case**: re-fetch actual order status and balances from the exchange for that specific execution, determine what actually happened, and either complete the reconciliation or transition to `UNWINDING`/`STUCK` as appropriate
  3. Only after all in-flight executions are resolved does the bot resume normal opportunity scanning
- Never assume a clean shutdown. Treat every startup as a potential post-crash recovery.
- Log a clear "recovery report" on every startup showing what (if anything) was found in-flight and how it was resolved, and send this via the alert channel — silent recovery is not acceptable, you need to know it happened.

### 2.4 Fee Calculator — Comprehensive Net-Profit Model

v1 used a flat "gross spread minus fees" estimate. That's not sufficient. The calculator must model, per opportunity:

1. **Maker/taker fee** at the account's actual current tier (fetch and cache fee schedule per exchange; tiers change with volume — don't hardcode)
2. **Order book depth walk**, not top-of-book price: simulate actually walking the book for the intended order size to get a realistic average fill price, not the best bid/ask
3. **Slippage buffer**: an additional safety margin beyond the depth-walk estimate, since the book can move between calculation and execution
4. **Latency-adjusted price decay**: factor in your measured average decision-to-fill latency (from Section 2.6) and discount expected profit accordingly — an opportunity that only clears the threshold at zero latency is not a real opportunity
5. **Withdrawal fees** (cross-exchange only) — flat fee per network/asset, fetched from exchange fee schedules, not assumed
6. **Network/gas fees** for any on-chain leg
7. **Funding fees** (Strategy C) — the funding payment itself is the intended profit source, but entry/exit trades still incur spot + futures trading fees that must be amortized over expected holding period
8. **Currency conversion costs** if a leg requires converting between quote currencies
9. **Opportunity cost / capital lock-up**: not required for v1 build, but the calculator should be structured so this can be added later without a rewrite

**Only flag an opportunity as valid if:**
`gross_spread − (all fees above) − slippage_buffer − latency_decay_estimate > minimum_profit_threshold`

Every rejected opportunity should still be logged (Section 16) with the breakdown of why it failed the threshold — this data is what lets you tune thresholds intelligently later instead of guessing.

### 2.5 Paper Trading — Realistic Simulation

v1's paper trading assumed clean fills at calculated prices. That overstates results and will give you false confidence. The simulator must:
- Fill against the **actual live order book depth** at simulation time (walk the book, same logic as the real fee calculator), not a flat mid-price assumption
- Inject **simulated latency**: delay the "decision" timestamp to "fill" timestamp by your measured/estimated real-world latency, and re-check whether the opportunity would still exist at that later timestamp using the live feed
- Simulate **partial fills probabilistically** — not every simulated order should assume 100% fill; model a distribution based on order size vs. available depth
- Simulate **occasional rejected/errored orders** (exchanges do have transient API failures) so the execution engine's error handling is actually exercised in paper mode, not just its happy path
- Track and report **simulated vs. naively-calculated slippage** side by side, so you can see how much the realism adjustments matter
- Run the exact same strategy/execution/risk-manager code paths as live trading — the only thing that differs is the exchange client's `place_order()` being swapped for a simulated fill. Do not fork logic between paper and live modes; that guarantees drift between what you tested and what you run for real.

### 2.6 Latency Monitor (supports 2.4 and 2.5)
- Timestamp every stage: market data received → opportunity detected → validation complete → order sent → fill confirmed
- Maintain a rolling average/percentile (p50/p95) of end-to-end latency per exchange and per strategy
- Feed this into the fee calculator's latency-decay estimate (2.4.4) and the paper trading simulator (2.5)
- Alert if latency exceeds a configurable threshold (may indicate network issues, exchange API degradation, or server resource contention)

---

## 3. Strategy A — Triangular Arbitrage (Single Exchange)

### 3.1 Concept
On one exchange, exploit temporary pricing inconsistency across three pairs, e.g.:
`USDT → BTC → ETH → USDT`
If `(1/BTC-USDT price) × (BTC-ETH price) × (ETH-USDT price) ≠ 1`, adjusted for fees, an arbitrage exists.

### 3.2 Requirements
- One exchange account with API access (recommend Binance or Kraken — deep liquidity, low fees, solid API docs)
- API key with **trade permission only** (never enable withdrawal permission on a bot's API key)
- WebSocket order book feeds for all three legs simultaneously

### 3.3 Algorithm
1. Subscribe to live order books for a curated list of 5–10 high-liquidity triangles
2. On every order book update, recalculate implied cross-rate using the comprehensive fee calculator (2.4), not a flat-fee shortcut
3. If net profit clears threshold, transition into the execution state machine (2.1) and fire leg 1
4. Proceed leg by leg, checking actual fill status after each — do not assume a submitted order = a filled order

### 3.4 Partial-Fill / Failure Handling (replaces v1's placeholder)
This is the actual failure-handling logic for triangular arb:

- **After leg 1 fills:** confirm actual filled quantity and average price (not the intended quantity — exchanges can partial-fill even "market" orders in thin books). Recalculate leg 2's target size based on what leg 1 actually delivered, not the original plan.
- **If leg 1 partially fills:** proceed with leg 2 sized to the actual filled amount if it's still profitable at that smaller size after fees; if not profitable at the smaller size, immediately unwind leg 1's partial fill (sell back what was bought) rather than proceeding into a now-unprofitable trade.
- **If leg 2 or leg 3 fails to fill within `execution_timeout_ms`:** cancel the resting order immediately, and unwind all previously-filled legs in reverse order via market orders, accepting the unwind cost as a bounded, known loss rather than holding unwanted inventory indefinitely.
- **If a leg partially fills and the remainder can't be filled within timeout:** treat the filled portion as the "actual trade," cancel the remainder, and either complete the triangle at the smaller size (if still profitable) or unwind.
- **Dust handling:** after unwinds, small residual balances ("dust") are expected — log them, don't treat them as reconciliation failures below a configurable dust threshold, but do track cumulative dust as a real (small) cost of doing business.
- **All of this runs inside the state machine (2.1):** partial fill → `PARTIAL_FAILURE` → `UNWINDING` → `UNWOUND` or `STUCK`. Every unwind attempt and outcome is logged with enough detail to reconstruct exactly what happened after the fact.

### 3.5 Config Parameters
```yaml
triangular:
  exchange: binance
  triangles:
    - [USDT, BTC, ETH]
    - [USDT, BTC, BNB]
    - [USDT, ETH, BNB]
  min_profit_threshold_pct: 0.15
  max_position_size_usd: 200
  execution_timeout_ms: 2000
  slippage_buffer_pct: 0.05
  partial_fill_min_viable_pct: 50   # below this % filled, unwind rather than adjust-and-proceed
```

---

## 4. Strategy B — Cross-Exchange Arbitrage

### 4.1 Concept
Buy an asset on Exchange A where it's cheaper, sell on Exchange B where it's more expensive. Requires capital **pre-positioned on both exchanges**.

### 4.2 Requirements
- Accounts + API keys (trade-only permission) on 2–3 exchanges
- Capital split and pre-funded on each exchange in both base asset and quote currency
- Reliable low-latency connection or cloud server

### 4.3 Algorithm
1. Maintain live order books across all connected exchanges via WebSocket
2. Compute spread using the comprehensive fee calculator (2.4), including withdrawal-fee awareness for eventual rebalancing
3. If profitable, execute buy on A and sell on B in parallel (async — see 12), not sequentially
4. If one leg fails, immediately attempt to reverse the filled leg via the execution state machine's unwind path
5. Route resulting balance changes through the **Cross-Exchange Inventory Manager** (Section 14)

### 4.4 Key Risks
- Highest operational complexity of the three; withdrawal/transfer delays mean capital is fixed to wherever it already sits
- Balance skew requires active rebalancing (cost + time)
- Exchange-specific counterparty and regulatory risk

### 4.5 Config Parameters
```yaml
cross_exchange:
  exchanges: [binance, kraken]
  pairs: [BTC/USDT, ETH/USDT]
  min_profit_threshold_pct: 0.25
  max_position_size_usd: 300
  rebalance_check_interval_min: 60
  rebalance_skew_threshold_pct: 20
```

---

## 5. Strategy C — Funding Rate ("Cash and Carry") Arbitrage

### 5.1 Concept
Long spot + short equal-notional perpetual futures, collecting funding rate payments while net directional exposure stays near zero.

### 5.2 Requirements
- One exchange offering both spot and perpetual futures for the same asset (Binance, Bybit, OKX)
- API key with trade permission on both spot and derivatives accounts
- Capital split roughly 50/50 between spot buy and futures margin

### 5.3 Entry/Exit Logic (replaces v1's simplistic snapshot check)

v1 checked current funding rate against a flat threshold. That's not enough — funding rate is noisy and can flip on a single reading. The real logic:

**Entry:**
1. Pull a **rolling historical window** of funding rate data (e.g., last 7–14 days of 8-hour readings), not just the current/next rate
2. Require the **trailing average** annualized funding rate to exceed the entry threshold, and require some measure of stability (e.g., funding hasn't flipped negative more than N times in the window) — a single high reading is not a signal, a sustained trend is
3. Check the **basis** (futures price vs. spot price gap) — a large basis can itself represent risk/cost on entry and exit, since you're transacting at whatever the current basis is when you open and close
4. Estimate expected holding period and amortize entry+exit trading costs over that period to confirm the trade clears net profit after costs, not just gross funding yield
5. Enforce a **minimum holding period** before re-evaluation — do not let the strategy flip in and out on short-term funding noise; each entry/exit costs real trading fees

**Exit (any of the following triggers a close):**
- Trailing average funding rate drops below a re-evaluation threshold (not just one bad reading)
- Basis risk exceeds a configured bound (spot and futures have diverged more than expected, suggesting elevated risk of a larger, harder-to-unwind gap)
- Margin ratio on the short leg approaches the liquidation-risk threshold (see Section 17 — this is a hard override that exits regardless of funding outlook)
- A maximum holding period is reached and conditions no longer clearly justify continuing (periodic re-evaluation against entry criteria)

**All entries and exits run through the execution state machine (2.1)** — this is a two-leg trade (spot + futures) and is subject to the same partial-fill and unwind logic as the other strategies.

### 5.4 Key Risks
- Funding rate can flip negative — mitigated by trailing-average entry logic and continuous re-evaluation, not eliminated
- Exchange counterparty risk
- Basis risk during volatility spikes
- **Liquidation risk on the short leg** — see Section 17 for dedicated monitoring; this is the risk most likely to actually hurt you in this strategy and gets its own subsystem, not just a config parameter

### 5.5 Config Parameters
```yaml
funding_rate:
  exchange: bybit
  symbols: [BTCUSDT, ETHUSDT]
  funding_history_window_days: 10
  min_trailing_annualized_funding_pct: 10
  max_negative_flips_in_window: 2
  max_basis_pct: 0.5
  min_holding_period_hr: 24
  max_holding_period_days: 14
  position_size_usd: 500
  margin_buffer_pct: 50
```

---

## 6. Risk Manager

### 6.1 Standard Controls (v1, retained)
- Max position size per trade
- Max daily loss — halt and alert if breached
- Max open positions concurrently
- Circuit breaker on repeated API errors or stale data
- Balance sanity check before every trade

### 6.2 Kill Switches (new — P0)
Three independent, layered kill switches, checked at the start of every cycle before any new opportunity is evaluated:

1. **Global kill switch** — stops all trading across all strategies and all exchanges. Master off switch.
2. **Per-strategy kill switch** — stops one strategy (e.g., halt triangular, leave funding-rate and cross-exchange running).
3. **Per-exchange kill switch** — stops all activity on one exchange (e.g., halt everything on Kraken if its API is degraded, leave Binance running).

**Implementation notes:**
- Kill switch state lives in the persistent state store (Section 10), not just in-memory — a switch flipped mid-run must survive a restart.
- Switches must be triggerable both programmatically (risk manager auto-trips one on breach of a threshold) and manually (via a Telegram/Discord command, or a simple flag file/DB row you can flip by hand without redeploying code).
- When any kill switch trips, in-flight executions still run through their unwind logic (Section 2.1/3.4) — a kill switch stops *new* opportunities, it does not abandon a position already mid-execution.
- Every kill switch trip (auto or manual) is logged and alerted immediately.

---

## 7. Infrastructure Requirements

| Component | Recommendation |
|---|---|
| **Hosting** | Cloud VPS close to exchange servers — AWS Tokyo/Singapore for Binance, AWS us-east for Coinbase/Kraken |
| **Language** | Python 3.11+ (CCXT ecosystem) with `asyncio` throughout (see Section 12) |
| **Key libraries** | `ccxt` / `ccxt.pro`, `websockets`, `pandas`, `pydantic`, `sqlalchemy`, `redis`, `python-telegram-bot` |
| **Database** | Postgres (recommended given the data model in Section 10 — SQLite is workable for a single-strategy prototype but will get cramped fast with reconciliation/execution logs) |
| **Fast state** | Redis — see Section 11 |
| **Secrets management** | `.env` + `python-dotenv` locally; a real secrets manager if deployed to cloud infra. Never commit API keys to git. |
| **Monitoring** | Telegram bot for real-time alerts; latency/error dashboards can be a simple Grafana setup later if you want it |
| **Uptime** | `systemd`/`supervisord` auto-restart; every restart runs the crash-recovery flow (Section 2.3) before resuming trading |

---

## 8. Async Execution (P1)

- The entire bot must be built on `asyncio` from the start, not retrofitted later. WebSocket order book feeds, REST calls, and order placement across multiple legs/exchanges all need to happen concurrently, not in a blocking sequential loop.
- Order placement for multi-leg trades (triangular, cross-exchange, funding-rate entry) must fire legs **concurrently** via `asyncio.gather()` or equivalent where the strategy calls for simultaneous execution (cross-exchange, funding-rate entry) — sequential firing defeats the purpose and widens the latency window the opportunity can disappear in.
- Use a dedicated `asyncio` task per exchange WebSocket connection, with its own reconnect/heartbeat logic, so one exchange's connection issue doesn't block others.
- Be deliberate about where synchronization is required (e.g., state machine transitions must be written before the next action) versus where true concurrency is wanted (parallel leg execution) — don't accidentally serialize things that need to be parallel, and don't accidentally race things that need to be sequential.

---

## 9. Go-Live Gate

A hard, automated gate between paper trading and live capital. The bot should refuse to place real orders (config-level lock, not just a manual decision) until all of the following are met:

1. **Minimum paper-trading duration**: e.g., 14–21 consecutive days of paper trading with no manual restarts that skipped the recovery flow
2. **Minimum sample size**: e.g., at least 50–100 simulated executions per strategy being evaluated — too few trades means you can't distinguish skill from noise
3. **Fee-adjusted profitability**: net simulated P&L positive over the evaluation window, using the realistic simulator (2.5), not the naive calculator
4. **Consistency check**: no single simulated trade or short cluster of trades accounts for the majority of simulated profit (a strategy that's profitable only because of one lucky spike is not validated)
5. **Max drawdown check**: simulated max drawdown stays within a pre-defined bound you're comfortable with in real capital
6. **Reconciliation clean run**: reconciliation process (2.2) has been running throughout paper trading with zero unexplained discrepancies (paper mode should still exercise this logic against simulated state)
7. **Manual sign-off**: even after 1–6 pass automatically, require an explicit manual confirmation step (e.g., a config flag you set by hand, or a confirmation command) before the bot will accept live-mode startup — the automation should make going live easy once earned, not automatic.

Implement this as `gate/go_live_gate.py`: a function that checks 1–6 against the trade journal and returns pass/fail with a report, called on any attempt to start the bot in live mode. Live mode startup aborts with a clear message if the gate isn't passed.

---

## 10. Database / Data Model

Minimum viable schema (Postgres). Names indicative — adjust as needed, but keep the entities:

- **`executions`** — one row per state-machine run (Section 2.1). Columns: `id`, `strategy`, `exchange(s)`, `state`, `created_at`, `updated_at`, `symbols`, `intended_size`, `actual_size`, `expected_profit`, `realized_profit`, `outcome` (completed/unwound/stuck/failed).
- **`execution_legs`** — one row per leg within an execution. Columns: `id`, `execution_id` (FK), `leg_number`, `exchange`, `symbol`, `side`, `intended_qty`, `filled_qty`, `avg_fill_price`, `fee_paid`, `order_id`, `status`, `submitted_at`, `filled_at`.
- **`opportunities`** — every evaluated opportunity, taken or not (Section 2.4 note). Columns: `id`, `strategy`, `detected_at`, `symbols`, `gross_spread_pct`, `net_profit_estimate`, `fee_breakdown` (JSON), `threshold_at_time`, `action_taken`, `execution_id` (nullable FK).
- **`balances_snapshot`** — periodic + post-execution balance snapshots per exchange, used by reconciliation. Columns: `id`, `exchange`, `asset`, `balance`, `snapshot_at`, `source` (scheduled/post-execution).
- **`reconciliation_log`** — Columns: `id`, `exchange`, `checked_at`, `expected_state` (JSON), `actual_state` (JSON), `discrepancy` (JSON), `severity`, `resolved`.
- **`funding_rate_history`** — Columns: `id`, `exchange`, `symbol`, `rate`, `annualized_pct`, `recorded_at` — needed for Strategy C's rolling-window logic (5.3).
- **`kill_switch_state`** — Columns: `scope` (global/strategy/exchange), `scope_value`, `is_tripped`, `tripped_by` (auto/manual), `tripped_at`, `reason`.
- **`system_events`** — general structured log for crash/recovery reports, kill switch trips, circuit breaker trips, latency alerts. Columns: `id`, `event_type`, `severity`, `payload` (JSON), `created_at`.
- **`margin_monitoring`** (Strategy C) — Columns: `id`, `position_id`, `exchange`, `symbol`, `margin_ratio`, `liquidation_price`, `checked_at`.

Recovery (2.3) works by querying `executions` for any row not in a terminal state on startup. Reconciliation (2.2) works by comparing the latest `balances_snapshot` per exchange/asset to the sum of confirmed `execution_legs`.

---

## 11. Redis / Fast State (P1)

Use Redis alongside Postgres for anything that needs sub-millisecond reads on the hot path (Postgres is for durable history, Redis is for "what's true right now"):
- **Live order book cache** — current best bid/ask and depth snapshot per exchange/symbol, updated on every WebSocket tick
- **Current position/exposure state** — fast lookup during opportunity evaluation, avoids a DB round-trip on every check
- **Distributed locks** — prevent two concurrent evaluation cycles from acting on the same opportunity or the same execution slot
- **Kill switch state (cached)** — read from Redis on the hot path, with Postgres as the durable source of truth synced on write
- Redis is a cache/accelerator here, not the system of record — every state-machine transition still writes through to Postgres (2.1) so nothing is lost if Redis restarts.

---

## 12. Stale-Data Protection (P1)

- Every order book update carries a timestamp; the orderbook manager rejects/flags data older than a configurable threshold (e.g., >500ms–1s depending on strategy) rather than letting a strategy act on stale prices.
- WebSocket connections carry heartbeat/ping-pong monitoring — if a connection goes quiet beyond expected interval, treat its data as stale immediately (don't wait for a hard disconnect event) and trigger reconnect logic.
- The comprehensive fee calculator (2.4) and execution state machine's `VALIDATING` step both re-check data freshness immediately before committing capital — a decision made on data that's since gone stale must be re-validated, not assumed still valid.
- Log every instance of stale-data rejection — a high frequency of these indicates a connectivity or infra problem worth fixing before it costs money.

---

## 13. Detailed Execution Logging (P1)

Beyond the trade journal in v1, log per-order-lifecycle detail:
- Every state transition with timestamp (already required by 2.1, called out here as a logging requirement)
- Latency breakdown per stage (feeds latency monitor, 2.6)
- Expected vs. actual fill price and quantity per leg, with slippage computed explicitly
- Full fee breakdown actually charged (from exchange fill data) vs. what was estimated pre-trade
- This log is what makes the Go-Live Gate's consistency checks (Section 9) possible, and what lets you actually debug a `STUCK` state after the fact instead of guessing.

---

## 14. Cross-Exchange Inventory Manager (P1, Strategy B)

A dedicated module (not just a periodic check) that:
- Tracks real-time balance per asset per exchange, sourced from the reconciliation process (2.2)
- Forecasts skew trend — if Exchange A is consistently accumulating the base asset over time, flag before it becomes a hard constraint on trade sizing
- Recommends (or, once trusted, automatically triggers — see 21) rebalancing transfers, factoring in withdrawal fee + network confirmation time as a cost against the trade opportunities it will forgo while capital is in transit
- Enforces a minimum balance buffer per exchange so the bot never gets fully skewed to one side and unable to take the next opportunity in either direction

---

## 15. Liquidation / Margin Monitoring (P1, Strategy C)

Given that liquidation risk is flagged as the most likely real-money risk in Strategy C (5.4), it gets its own subsystem rather than a single config parameter:
- Continuously poll margin ratio / distance-to-liquidation on the short futures leg (via exchange API, not inferred)
- Write to `margin_monitoring` table (Section 10) on a tight interval (e.g., every 30–60s while a position is open)
- Configurable warning threshold (alert only) and a hard action threshold (auto-add margin from available balance, or auto-close the position) — the hard threshold should trigger regardless of what the funding-rate exit logic (5.3) says; liquidation risk overrides funding outlook
- This monitor's action, when triggered, routes through the execution state machine's unwind path like any other position close

---

## 16. P2 — Optimization (Not Required for Initial Build)

These improve performance/reach once the P0/P1 foundation is proven and running live successfully. Do not start these before the core is solid — they add complexity without changing whether the bot is safe to run.

18. **Native WebSocket optimization** — move latency-critical feeds from CCXT's unified layer to native per-exchange WebSocket implementations for lower overhead, once you've identified via the latency monitor (2.6) that CCXT's abstraction is the bottleneck.
19. **More triangular pairs** — expand beyond the initial 5–10 curated triangles once the core set is proven profitable after fees.
20. **More exchanges** — add exchanges to Strategy A/B/C only after the current set is stable; each new exchange multiplies operational surface area (API quirks, fee schedules, uptime characteristics).
21. **Automated rebalancing** — move the Cross-Exchange Inventory Manager (14) from recommend-and-confirm to fully automatic transfers, once you trust its skew forecasting.
22. **Execution optimization** — smart order routing, iceberg/TWAP-style order splitting for larger sizes, and other execution-quality improvements once basic execution is reliable and you have enough logged data (13) to know where the real slippage is coming from.

---

## 17. What You Need to Get Started (Action Checklist)

1. **Pick your exchange(s)** — for Strategy C (start here), pick one exchange with both spot + perpetuals; check jurisdiction eligibility.
2. **Create API keys** — trade-only, no withdrawal permission, IP-whitelisted if supported.
3. **Fund a small test account** — capital you're fully prepared to lose during build/test, not your intended full stake.
4. **Provision infra** — VPS + Postgres + Redis. Still cheap at this scale (a $10–40/mo VPS handles Postgres+Redis+bot for early testing).
5. **Hand this document to your coding agent** along with your exchange choice, starting capital, and risk tolerance (max daily loss, position size comfort).
6. **Build order**: shared core (Section 2, including state machine/reconciliation/recovery) → Strategy C → paper trade through the full Go-Live Gate (Section 9) → Strategy A → gate again → live consideration → Strategy B last.
7. **Do not bypass the Go-Live Gate.** It exists specifically so "let's just go live a bit early" isn't a judgment call made under the influence of impatience.

---

## 18. Explicitly Out of Scope / Not Provided

- No prediction of specific profit amounts or timelines.
- No flash-loan/MEV bot specs — different risk/complexity profile, can be a separate conversation later.
- No tax or regulatory advice — consult a professional for your jurisdiction.
- No guarantee any exchange's ToS permits bot trading on a retail account — verify before deploying.
