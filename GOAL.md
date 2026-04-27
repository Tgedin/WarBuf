# WarBuf — Goal

## What we are doing and why

Run for **one month** (May 4 → June 4, 2026) using an **IBKR paper trading account** with €3,000, then switch to the **real IBKR account on June 4**.

The paper phase is not a demo. It uses the real IBKR infrastructure (CP Gateway, real order flow, real session keepalive) with virtual money. The same code — `broker/ibkr.py`, the scheduler, the stop-loss logic — runs in both phases. The only difference between paper and live is the account ID in `.env`.

WarBuf is a **mini agentic investment system**: math selects, LLM audits, human controls one file (`rules.yaml`). All data is recorded and surfaced on the dashboard so that every week you can see what the bot did, why, and improve it — bit by bit.

## Paper vs live — the only difference

| Phase | `IBKR_ACCOUNT_ID` in `.env` | Money |
| --- | --- | --- |
| Paper (May 4 → June 4) | `DU...` (IBKR paper account) | Virtual |
| Live (June 4 onwards) | `U...` (IBKR live account) | Real |

Everything else is identical: same gateway URL, same code, same scheduler, same stop-loss, same fees.

## Why IBKR paper account is better than local simulation

The previous approach simulated fills with a fixed 0.1% slippage and prior-close prices. That has two problems:
1. The code path exercised was `broker/paper.py` — *not* the code that runs live. Bugs in `broker/ibkr.py` would only surface at go-live with real money.
2. Slippage, order replies, session keepalive, and gateway edge cases cannot be discovered until live trading begins.

With the IBKR paper account, every scheduled job, every order, every stop-loss check, and every DB write runs through `broker/ibkr.py` — the exact same path as live. Operational bugs cost €0 to discover.

## Portfolio structure on May 4

| Bucket                                 | Allocation | EUR amount |
| -------------------------------------- | ---------- | ---------- |
| Core ETFs — SPY (25%) + QQQ (15%)      | 40%        | €1,200     |
| Satellite — up to 5 stocks, ~€300 each | 50%        | €1,500     |
| Cash floor (never deployed)            | 10%        | €300       |

## Go-live criterion — June 4

If the paper phase ran without critical bugs and net P&L is not catastrophically negative → change `IBKR_ACCOUNT_ID` in `.env` from `DU...` to `U...` and restart the bot. No code changes required.

No minimum return threshold required. The goal of the paper phase is operational validation, not profit.

## Success metric (live, 3 months after June 4)

Net return after IBKR fees > SPY total return over the same period, with max drawdown < 15%.

## What is compared at month-end

- Portfolio tab: actual EUR value vs cost basis
- Performance tab: portfolio curve vs SPY
- Forecasts tab: predicted range (SPY ±1σ) vs actual outcome
