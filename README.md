# Crypto Arbitrage Bot

An institutional-grade, asynchronous crypto arbitrage bot designed with extreme safety constraints, robust reconciliation, and distributed execution state.

Based on the `crypto_arbitrage_bot_spec_v2.md` architecture.

## Features

- **Execution State Machine**: Persistent, atomic transition tracking of every single opportunity execution.
- **Triple-Layer Kill Switches**: Configurable global, strategy, and per-exchange auto-halting mechanisms.
- **Three Core Strategies**:
  - Strategy A: Triangular Arbitrage (Intra-exchange)
  - Strategy B: Cross-Exchange Arbitrage
  - Strategy C: Funding Rate Arbitrage (Cash-and-Carry)
- **Go-Live Gate**: A strict programmatic gate preventing live trading until paper trading conditions, sample sizes, and consistency checks are mathematically validated.
- **Redis Fast State**: Sub-millisecond distributed locks and orderbook caching on the hot path.
- **Postgres Persistence**: Deep audit logging for executions, legs, unviable opportunities, margin tracking, and continuous balance reconciliation.

## Setup & Installation

1. **Prerequisites**: Python 3.11+, Redis server, Postgres (or use the built-in SQLite wrapper for MVP testing).
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Environment Variables**:
   Create a `.env` file in the root directory (never commit this to git):
   ```ini
   # API Keys (TRADE PERMISSIONS ONLY - NEVER ENABLE WITHDRAWALS)
   BINANCE_API_KEY=your_key_here
   BINANCE_API_SECRET=your_secret_here
   
   # Infrastructure
   DATABASE_URL=sqlite+aiosqlite:///bot_state.db
   REDIS_URL=redis://localhost
   ```

## Running the Bot

By default, the bot initializes completely mocked against a realistic **Paper Trading Simulator**. It simulates latency, probabilistic partial fills, and actual depth-walking slippage.

To launch the bot daemon:
```bash
python main.py
```

## Running the Test Suite

The project includes a comprehensive test suite across safety monitors, database integrity, the execution engine, and strategy math.

```bash
pytest
```

## Safety & Constraints (GEMINI.md Rules)

- **No Hardcoded Keys**: All keys MUST use the `.env` file.
- **No Withdrawals**: Never enable API key withdrawal access. The Cross-Exchange manager only recommends rebalances; it will not execute them on your behalf.
- **No Silent Failures**: All stale data rejections, missed fills, and reconciliation variances trigger `Notifier` alerts and save to the database. If an execution unwinds and fails, it enters a `STUCK` state and alerts for manual review.
- **Paper Trading Parity**: The paper trading simulator wraps the CCXT methods dynamically. Live trading runs the exact same execution paths.

## Project Structure

- `core/`: State machines, db persistence, reconciliation engine, fee calculators, and kill switches.
- `gate/`: The Go-Live programmatic checklist.
- `paper_trading/`: Realistic exchange wrapping with probabilistic simulation.
- `strategies/`: Arbitrage execution logic.
- `tests/`: Pytest suite covering 100% of the core architecture.

## Disclaimer

This is not financial advice. Automated trading involves significant risk. You are responsible for your own capital, tax reporting, and compliance with local regulations and exchange Terms of Service. Always validate strategies in paper-trading mode before committing real capital.
