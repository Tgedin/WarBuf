# WarBuf — Goal

## What we are doing and why

Paper trade for **one month** (May 4 → June 4, 2026) with €3,000, then deploy **real money on IBKR on June 4**.

The paper phase is not a demo. It is a dry run that must be as close to real IBKR execution as possible so that the results at month-end are meaningful.

WarBuf is a **mini agentic investment system**: math selects, LLM audits, human controls one file (`rules.yaml`). All data is recorded and surfaced on the dashboard so that every week you can see what the bot did, why, and improve it — bit by bit.

## Paper trading must simulate real IBKR conditions

| Parameter                                              | How it is simulated                                                    |
| ------------------------------------------------------ | ---------------------------------------------------------------------- |
| IBKR fee structure (SEC + FINRA + commission)          | `core/fees.py` — computed on every trade                               |
| Whole shares only (IBKR has no fractional shares)      | `broker/paper.py` — qty floored to `int`                               |
| Bid-ask slippage (~0.1% on liquid US equities)         | `broker/paper.py` — buy fills 0.1% above last close, sell 0.1% below   |
| EUR cost basis tracking                                | `db.py` — `avg_cost_basis_eur`, `total_fees_eur` per position          |
| Cash balance that depletes on buys / recovers on sells | `db.py` — `portfolio_cash` table, `adjust_cash()` on every trade       |
| NYSE market hours and holiday guard                    | `main.py` — `_is_nyse_holiday()`, `_is_nyse_trading_hours()`           |
| EUR/USD conversion rate                                | `rules.yaml` — `eur_usd_rate`, updated manually when rate drifts       |
| MKT DAY order semantics                                | Paper fills at prior close; real IBKR queues MKT DAY for 09:30 ET open |

## Portfolio structure on May 4

| Bucket                                 | Allocation | EUR amount |
| -------------------------------------- | ---------- | ---------- |
| Core ETFs — SPY (25%) + QQQ (15%)      | 40%        | €1,200     |
| Satellite — up to 5 stocks, ~€300 each | 50%        | €1,500     |
| Cash floor (never deployed)            | 10%        | €300       |

## Go-live criterion — June 4

If paper execution ran without critical bugs and net P&L is not catastrophically negative → flip `paper_mode: false` in `rules.yaml` and connect IBKR gateway.

No minimum return threshold required. The goal of the paper phase is operational validation, not profit.

## Success metric (live, 3 months after June 4)

Net return after IBKR fees > SPY total return over the same period, with max drawdown < 15%.

## What is compared at month-end

- Portfolio tab: actual EUR value vs cost basis
- Performance tab: portfolio curve vs SPY
- Forecasts tab: predicted range (SPY ±1σ) vs actual outcome
