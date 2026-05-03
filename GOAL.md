# WarBuf — Goal

## What we are doing and why

Build a **robust IBKR paper-trading machine** and refine it through repeated paper-trading cycles.

1. Start with **EUR 300** of paper capital.
2. Deploy against the real IBKR paper infrastructure and observe actual operational behavior.
3. After each deployed paper window, assess what to keep, what to change, and what to improve.
4. If one month is not enough, extend the paper cycle. If the machine proves reliable, increase paper capital gradually.

The paper phase is not a demo. It uses the real IBKR infrastructure (CP Gateway, real order flow, real session keepalive) with virtual money. The same code — `broker/ibkr.py`, the scheduler, the stop-loss logic — is the code path we want to harden end-to-end.

WarBuf is a **mini agentic investment system**: math selects, LLM audits, human controls one file (`rules.yaml`). All data is recorded and surfaced on the dashboard so that every week you can see what the bot did, why, and improve it — bit by bit.

Operational intent: give the bot real autonomy to deploy capital according to rules and risk controls, while minimizing unnecessary idle cash (only the defined safety cash floor should remain uninvested).

Evaluation intent: this is not only a return test; it is primarily a realism, reliability, and refinement test of IBKR paper behavior under real operational constraints.

## Paper trading environment

| Scope                               | `IBKR_ACCOUNT_ID` in `.env`  | Money   |
| ----------------------------------- | ---------------------------- | ------- |
| Paper validation and refinement run | `DU...` (IBKR paper account) | Virtual |

Current documented scope is paper trading only.

## Why IBKR paper account is better than local simulation

The previous approach simulated fills with a fixed 0.1% slippage and prior-close prices. That has two problems:

1. The code path exercised was `broker/paper.py` — _not_ the same broker path we want to harden end-to-end.
2. Slippage, order replies, session keepalive, and gateway edge cases cannot be discovered in a local-only simulator.

With the IBKR paper account, every scheduled job, every order, every stop-loss check, and every DB write runs through `broker/ibkr.py` against IBKR's paper infrastructure. Operational bugs cost €0 to discover.

## Validation stages

### Initial paper cycle (EUR 300)

Primary objective: operational validation.

- broker/auth/session stability
- order flow safety and guardrails
- fee-model realism against published IBKR commission schedules
- low idle cash above the safety floor
- usefulness of the dashboard and review process

### Refinement cycles

Primary objective: repeated improvement after observing real paper-trading behavior.

- deploy the paper stack
- observe decisions, fills, failures, and idle cash
- decide what to keep, what to change, and what to improve
- rerun with better rules, better tests, or better UX

### Optional paper scale-up

Primary objective: test the same machine at larger paper size only after it has earned that complexity.

- same risk controls and autonomy model
- evaluate whether larger paper capital changes fee drag, position sizing quality, or benchmark tracking

## Capital discipline

- Start with EUR 300 of paper capital.
- Keep only the intentional safety cash floor idle.
- Avoid over-fragmentation: at small size, keep position count low enough that fees and whole-share constraints do not dominate learning.
- Increase paper capital only after the scorecard justifies it.

## Progression criterion

After each paper cycle, choose one of these evidence-based actions:

1. Keep the same configuration and run another paper cycle.
2. Refine the system and start a new paper cycle.
3. Increase paper capital only if the scorecard passes.

Real-money deployment is not part of the current documented scope.

## Success metric for the current project

- Stable autonomous paper operation without unsafe behavior.
- Reliable auth/session handling and clear failure modes.
- Fee model remains decision-useful against published IBKR commissions.
- Minimal unnecessary idle cash.
- Benchmark comparison improves across refinement cycles, not just one short window.

## What is compared at each review window

- Portfolio tab: actual EUR value vs cost basis
- Performance tab: portfolio curve vs SPY
- Forecasts tab: predicted range (SPY ±1σ) vs actual outcome
- auth uptime and session stability
- preflight safety behavior under auth failure
- modeled-fee vs published-commission consistency
- what to keep, what to change, and what to improve in the next paper cycle
