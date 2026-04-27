# AGENTS.md — WarBuf Coding Guide

**Repository**: https://github.com/Tgedin/WarBuf

> **Primary reference**: Read `GOAL.md` first. Every change you make must serve the goal
> defined there — paper trading that faithfully simulates real IBKR execution so that
> the June 4 go-live decision is based on real data.

> **Maintenance rule**: Update this file for every substantial modification to the codebase —
> new modules, changed architecture, altered strategy parameters, added dependencies,
> or modified DB schema. An outdated AGENTS.md is worse than none.

---

## What This Bot Does

Autonomous long-term investment bot (hold periods: months–years).
Every Monday 09:00 Europe/Madrid it re-scores held positions and watchlist tickers.
First Monday of the month it runs the full buy pipeline and sends a forecast.

**Not a day-trading bot.** Math decides. LLM analyses. Human controls `rules.yaml`.

---

## Repo Structure

```
WarBuf/
├── AGENTS.md          ← you are here; update on every substantial change
├── WarBuf.md          ← full project spec and strategy reference
├── rules.yaml         ← single control surface (no code edits needed for tuning)
├── main.py            ← APScheduler entry point; weekly_job + monthly_job + nightly_backup_job + cache_prewarm_job
│                        Pure helpers: _detect_sell_trigger, _is_nyse_holiday, _is_nyse_trading_hours, _is_first_monday
├── db.py              ← SQLite persistence (5 tables, WAL mode, no ORM)
├── reporter.py        ← plain-text email reports (5-line weekly ping + monthly forecast)
├── dashboard.py       ← Streamlit dashboard (Portfolio, Weekly Report, Trades, Decisions, Performance, Forecasts)
│
├── core/              ← pure functions only — zero I/O, fully testable without mocks
│   ├── scorer.py      ← 4-factor composite math (the strategy heart)
│   ├── fees.py        ← IBKR fee calculation (SEC + FINRA + commission)
│   ├── market.py      ← yfinance wrapper + 24h file cache (.cache/)
│   ├── screener.py    ← Tier 1 hard filters + Tier 2 scoring pipeline
│   ├── agent.py       ← LLM analysis layer (Tier 3); produces AnalysisReport per ticker
│   └── llm_provider.py← LiteLLM wrapper; temperature=0.1 fixed
│
├── broker/
│   ├── base.py        ← BrokerInterface(ABC); swap broker = one new file
│   ├── paper.py       ← paper trading; whole-share qty, 0.1% slippage, logs to SQLite, never touches real money
│   └── ibkr.py        ← IBKR Web API via plain requests (no ib_insync — archived 2024)
│
├── tests/             ← 231 tests; all external I/O mocked
│   ├── test_scorer.py           ← 19 tests
│   ├── test_fees.py             ← 15 tests
│   ├── test_screener.py         ← 17 tests  (passes_hard_filters)
│   ├── test_screener_pipeline.py← 6 tests   (run_tier1_tier2 end-to-end)
│   ├── test_paper_broker.py     ← 9 tests
│   ├── test_agent.py            ← 42 tests (all LLM calls mocked; weekly + monthly + memory + rules_context)
│   ├── test_db.py               ← 38 tests
│   ├── test_market.py           ← 16 tests (yfinance mocked)
│   ├── test_llm_provider.py     ← 9 tests
│   ├── test_ibkr.py             ← 17 tests (requests.Session mocked)
│   ├── test_reporter.py         ← 13 tests (smtplib mocked)
│   └── test_main.py             ← 12 tests (_detect_sell_trigger pure function)
│
├── Dockerfile         ← single-stage Python 3.12-slim image
├── docker-compose.yml ← warbuf + dashboard + caddy on one Hetzner CX23 instance
└── .github/
    └── workflows/
        └── ci.yml     ← GitHub Actions: pytest + coverage ≥ 85%
```

---

## Architecture Principles

### `core/` is pure

All modules in `core/` are pure functions with zero I/O. They accept plain Python
values and return plain Python values. This means they are testable without any
mocking of network or filesystem.

Side effects (DB writes, HTTP calls, email) live only in `main.py`, `db.py`,
`reporter.py`, and `broker/`.

### One control surface

`rules.yaml` is the only file the user ever edits. Every tunable parameter —
factor weights, filters, model name, paper mode — lives there.
`main.py` loads it fresh on every job run so changes take effect without restart.

### Broker is swappable

`BrokerInterface` (abstract) in `broker/base.py` defines `get_positions()`,
`place_order()`, `get_cash_usd()`. Adding a new broker = one new file implementing
that interface. No other code changes.

### Fail-open on LLM

If `call_llm` raises, `analyse_candidates` returns neutral `AnalysisReport` objects
(not vetoed, confidence=low). The math score governs. The system never blocks on LLM.

### `paper_mode: true` is the safety lock

`broker/paper.py` generates `PAPER-{uuid8}` order IDs and logs to SQLite.
No order reaches IBKR unless `paper_mode: false` in `rules.yaml` AND
`IBKRBroker` is instantiated. Both conditions must be true simultaneously.

### Dashboard is the primary reporting surface

The Streamlit dashboard (`dashboard.py`) is where all data is consumed.
The weekly email is a 5-line ping that links to the dashboard URL.
Set `DASHBOARD_URL` in `.env` so the email link works.

Deep links work via `?page=` query param — the dashboard reads it on load and opens the correct tab directly.
Email links use page-specific anchors:

- Weekly email → `{DASHBOARD_URL}?page=Weekly+Report`
- Monthly email → `{DASHBOARD_URL}?page=Forecasts`

Valid page values: `Portfolio`, `Weekly+Report`, `Trades`, `Decisions`, `Performance`, `Forecasts`.

**Dashboard tab summary:**

| Tab               | Purpose                                                                                                                                                           |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Portfolio**     | Per-position live P&L cards (current value, gross gain, net gain after fees); total live value vs cost basis. Prices fetched via yfinance, 1h cache.              |
| **Weekly Report** | Delta-only view: score movements table (prev → curr score, Δ per ticker), active vetoes, last 20 trades. No duplication with other tabs.                          |
| **Trades**        | Full trade log with paper/live tag.                                                                                                                               |
| **Decisions**     | All LLM decisions as expandable cards. Header shows score trend (oldest→newest, last 5). `self_critique` styled as warning box; `algorithm_feedback` as info box. |
| **Performance**   | Returns vs SPY chart + drawdown-from-peak chart. Header metrics: alpha vs SPY, max drawdown, WoW change.                                                          |
| **Forecasts**     | Monthly forecast vs actual with hit rate and beat-SPY summary.                                                                                                    |

**Sidebar** always shows:

- Paper mode badge (if applicable)
- Macro regime badge: 📈 RISK ON / 🔴 RISK OFF — SPY vs 200-day SMA, 1h cached via yfinance

---

## Strategy Reference

### 4-Factor Composite Score

```
score = 0.35 × quality_rank
      + 0.25 × value_rank
      + 0.25 × momentum_rank
      + 0.15 × profitability_rank
```

All factors are cross-sectionally ranked to [0, 1] before combining.
`None` → rank 0. Single value → rank 0.5 (neutral).

| Factor        | Formula                            | Source                |
| ------------- | ---------------------------------- | --------------------- |
| Quality       | ROE × FCF_margin × (1 − D/E_norm)  | Fama-French           |
| Value         | Earnings Yield = 1 / PE            | Graham                |
| Momentum      | 12-month return, skip last 30 days | Jegadeesh-Titman 1993 |
| Profitability | Gross Profit / Assets              | Novy-Marx 2013        |

### Macro Guard

No new buys when SPY < its 200-day SMA (Faber 2007).
Checked at the start of every `monthly_job`.

### Allocation

- 40% Core ETFs (SPY 25%, QQQ 15%) — always held, never scored
- 50% Satellite (≤5 stocks, ~10% each) — scored monthly
- 10% Cash floor — never deploy below this

### Sell Triggers (checked weekly — auto-executed)

- **Stop-loss**: position down ≥ `stop_loss_pct` (15%) from cost basis → **full exit** (`place_order(ticker, "sell", qty × price)`)
- **Score collapse**: composite score drops > `score_collapse_delta` (0.25) vs the previous week's decision → **50% trim** (`place_order(ticker, "sell", qty × price / 2)`)

Stop-loss takes priority over score collapse when both conditions fire simultaneously.
Detection logic lives in `_detect_sell_trigger()` in `main.py` (pure function, no I/O — fully unit tested).

### Scheduler reliability

All APScheduler jobs have `misfire_grace_time=3600` (1 h). If the Docker container restarts within
an hour of a scheduled run, APScheduler fires the missed job immediately on restart instead of
silently skipping it.

Both `weekly_job` and `monthly_job` check `_is_nyse_holiday(date.today(), rules["nyse_holidays"])`
at the top; if the date appears in the ISO-date list in `rules.yaml`, the job logs a notice and
returns immediately. Update `nyse_holidays` each December.

When `paper_mode: false` and the monthly order fires before NYSE open (09:00 Madrid ≈ 03:00 ET),
`_is_nyse_trading_hours()` detects the pre-open window and logs a warning.
The MKT DAY order still submits — IBKR queues it for execution at the 09:30 ET open bell.

---

## Database Schema (`portfolio.db`)

SQLite, WAL mode. Schema auto-created on first open via `db.py`.
**Migrations** are applied automatically on every open via `_apply_migrations()` — safe on existing databases (uses `ALTER TABLE … ADD COLUMN` wrapped in try/except, so re-running is idempotent).

| Table         | Purpose                                            |
| ------------- | -------------------------------------------------- |
| `decisions`   | Every scoring decision with full LLM report fields |
| `trades`      | Every executed trade with fees + EUR fields        |
| `positions`   | Current holdings (auto-updated on each trade)      |
| `performance` | Weekly portfolio vs benchmark snapshots            |
| `forecasts`   | Monthly forecast → actual tracking                 |

**`db.get_recent_decisions(ticker, limit=3)`** — returns the N most recent decision
rows for a ticker as `list[dict]`. Used by `monthly_job()` to inject historical memory
into the LLM explore prompt.

### `trades` columns (relevant additions)

| Column         | Type | Notes                                        |
| -------------- | ---- | -------------------------------------------- |
| `price_eur`    | REAL | `price_usd / eur_usd_rate` at execution time |
| `eur_usd_rate` | REAL | Rate used at execution time (DEFAULT 1.0)    |

### `positions` columns (relevant additions)

| Column               | Type | Notes                                         |
| -------------------- | ---- | --------------------------------------------- |
| `avg_cost_basis_eur` | REAL | Volume-weighted avg entry price in EUR        |
| `total_fees_eur`     | REAL | Cumulative IBKR fees in EUR for this position |

**`rules_hash`** on every `decisions` row = SHA-256 first 16 chars of `rules.yaml`.
This gives a full audit trail: you can always know which strategy version produced
which decision.

**`data_gaps` column** in `decisions` is a JSON array (stored as TEXT). Query it
across months to find what the LLM consistently wishes it had — those are the gaps
worth closing in the pipeline.

**`data_request` column** in `decisions` is a TEXT field — one concrete, actionable
data fetch the LLM wants for the next run (e.g. "fetch insider ownership pct"). Stored
alongside `data_gaps` but more specific and machine-actionable.

---

## EUR-Native Accounting

The user deposits in EUR. The bot tracks the full EUR cost basis for every trade
so that reports reflect what the investor actually spent.

### How it flows

1. `rules.yaml` — `eur_usd_rate: 1.08` (update manually when rate drifts materially)
2. `main.py` — reads `eur_usd_rate` from rules and passes it to:
   - `PaperBroker(db, eur_usd_rate=…)` — so paper trades record EUR cost
   - `db.record_trade(…, eur_usd_rate=…)` — stores `price_eur` + `eur_usd_rate`
   - `send_weekly_digest(…, eur_usd_rate=…)` for the header line
3. `db._update_position()` — computes and persists `avg_cost_basis_eur` and `total_fees_eur`
4. `weekly_job()` — calls `get_last_price(ticker)` per position, converts to EUR,
   computes `gross_gain_eur`, `net_gain_eur`, `return_pct`, and passes them to the
   position display dict for the weekly email
5. `reporter.send_weekly_digest()` — shows EUR/USD rate in header; each position line
   shows `value €X | +€X gross (+X%) | net +€X | fees €X` when P&L data is available

### Querying EUR data

```bash
# All trades with EUR cost
sqlite3 portfolio.db "SELECT ticker, side, qty, price_usd, price_eur, eur_usd_rate, date FROM trades ORDER BY date DESC LIMIT 20;"

# Current EUR cost basis and fees per position
sqlite3 portfolio.db "SELECT ticker, qty, avg_cost_basis_eur, total_fees_eur FROM positions;"
```

---

## LLM Layer (`core/agent.py`)

Two modes — different token budgets, different prompts, different purposes:

### Weekly veto-only pass (`analyse_weekly`)

Called from `weekly_job()` for each held position. Fast (~256 tokens).
Single-turn prompt returns `{vetoed, veto_reason}` per ticker.
Veto only for: active fraud / enforcement / revenue -20% YoY.

### Monthly full agentic analysis (`analyse_candidates`)

Called from `monthly_job()` for screened candidates. Two-turn (~2500 tokens):

1. **Explore turn** — model flags data gaps, anchoring risks, score/news contradictions
2. **Conclude turn** — produces full `AnalysisReport` per ticker with memory audit

**Historical memory injection**: `_build_memory_block()` formats the last 3 decision rows
per ticker from the DB into the explore prompt, so the LLM can audit its own prior
reasoning (bull/bear cases, self-critique, score trajectory).
**`data_request` is included in the memory block** — each prior decision's `data_request`
field is appended, so the LLM sees what it previously asked for and can follow up or
acknowledge the gap is still open.

**Strategy rules injection**: `_build_rules_block()` formats the active `rules.yaml`
parameters (factor weights, hard filters, allocation limits, risk thresholds) into the
explore prompt. The LLM can critique whether the current weights suit the candidates
and flag poorly calibrated parameters as part of its `algorithm_feedback`.

The LLM produces one `AnalysisReport` per candidate. Fields:

| Field                | Use                                                                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `vetoed`             | True only for fraud/enforcement/revenue -20% evidence                                                                                                  |
| `veto_reason`        | Concrete evidence cited for veto                                                                                                                       |
| `bull_case`          | Why the fundamental thesis holds                                                                                                                       |
| `bear_case`          | Most concrete near-term risk                                                                                                                           |
| `data_gaps`          | What the model wishes it had (actionable for pipeline improvement)                                                                                     |
| `data_request`       | Explicit request for one specific data item next run                                                                                                   |
| `model_confidence`   | `high` / `medium` / `low`                                                                                                                              |
| `confidence_reason`  | One-sentence justification                                                                                                                             |
| `self_critique`      | Where the model's own reasoning may be anchored or wrong                                                                                               |
| `algorithm_feedback` | Which factor(s) are least reliable for this stock, what weight or data change would improve signal quality — names the raw metric and proposes the fix |

**Token budgets** (in `rules.yaml`):

- `llm_max_tokens_weekly: 256` — veto-only, one turn
- `llm_max_tokens_monthly: 8000` — full two-turn agentic (thinking tokens + 5 JSON objects)

**Current model config**:

- `llm_model: github_copilot/gpt-4o` — weekly veto pass (fast, single turn, free via Copilot)
- `llm_model_monthly: openrouter/qwen/qwen3-235b-a22b` — monthly full analysis (primary; hybrid thinking MoE, ~$0.02/month, GPQA 70%)
- `llm_model_monthly_fallbacks: [openrouter/deepseek/deepseek-r1-0528, github_copilot/gpt-4o, github_copilot/gpt-4o-mini]` — tried in order if primary fails
- `llm_max_tokens_monthly: 8000` — raised to accommodate thinking tokens + 5 JSON objects

**OpenRouter setup** (required for monthly analysis):
Set `OPENROUTER_API_KEY` in `.env`. Get key at `https://openrouter.ai/settings/keys` (pay-as-you-go, Revolut/Visa accepted).
LiteLLM picks up `OPENROUTER_API_KEY` automatically for the `openrouter/` prefix. No code changes needed.

**OpenRouter thinking model comparison** (as of April 2026):

| Model                                   | Input /1M | Output /1M | GPQA | Notes                                |
| --------------------------------------- | --------- | ---------- | ---- | ------------------------------------ |
| `openrouter/qwen/qwen3-235b-a22b`       | $0.46     | $1.82      | 70%  | **Current primary** — best value     |
| `openrouter/deepseek/deepseek-r1-0528`  | $0.70     | $2.50      | 71%  | Fallback #1 — best on math/logic     |
| `openrouter/deepseek/deepseek-v4-pro`   | $0.44     | $0.87      | n/a  | Cheap reasoning, 1M context          |
| `openrouter/deepseek/deepseek-v4-flash` | $0.14     | $0.28      | n/a  | Budget — configurable reasoning mode |

**Note on Claude models**: Claude Sonnet 4.6 is accessible in VS Code Copilot chat but NOT via the GitHub Models API (`models.inference.ai.azure.com`). Available API models: `gpt-4o`, `gpt-4o-mini`, `Meta-Llama-3.1-405B-Instruct`. Use `github_copilot/<model_id>` prefix.

**GitHub Copilot models** (zero marginal cost — included with any Copilot subscription):
Prefix the model name with `github_copilot/` in `rules.yaml` and set `GITHUB_TOKEN` in `.env`.
Uses the **GitHub Models** endpoint (`https://models.inference.ai.azure.com`).
Requires a **fine-grained PAT** with `Models: Read-only` permission (Permissions → Other permissions → Models).
Create at: `github.com/settings/tokens/new` — no repository access needed.

| Copilot model                      | Best for                                             |
| ---------------------------------- | ---------------------------------------------------- |
| `github_copilot/gpt-4o`            | Best all-around; default recommendation              |
| `github_copilot/gpt-4o-mini`       | Lighter/faster; fine for quick screens               |
| `github_copilot/claude-3.5-sonnet` | Strong structured output; good default               |
| `github_copilot/claude-3.7-sonnet` | Most nuanced analysis; preferred for monthly         |
| `github_copilot/o3-mini`           | Deliberate chain-of-thought; great for self_critique |

For a small instance, use the lighter models above and keep prompt/output tokens low.
The bot’s current pipeline is already lean: roughly 10k tokens/month at the default
analysis depth. That means a small instance can work well if you choose `gpt-4o-mini`
or `o3-mini` instead of a heavyweight model.

Local LiteLLM hosting is also possible in this project, but it requires a compatible
LiteLLM endpoint and corresponding `litellm` configuration. The current code is
provider-agnostic, so a hosted local instance can be used by setting `llm_model`
appropriately and ensuring `litellm` can resolve that backend.

Swap with one line in `rules.yaml`. No code changes.

---

## Key Dependencies

| Package       | Why                                            | Alternative considered                             |
| ------------- | ---------------------------------------------- | -------------------------------------------------- |
| `yfinance`    | Free market data + fundamentals                | Alpha Vantage (paid)                               |
| `litellm`     | LLM provider abstraction (swap model = 1 line) | Direct API calls (no swap)                         |
| `apscheduler` | Cron-style weekly/monthly jobs                 | systemd timer (no Python control)                  |
| `requests`    | IBKR Web API calls                             | `ib_insync` — archived March 2024, author deceased |
| `pyyaml`      | Load `rules.yaml`                              | `tomllib` (no comment support)                     |

No ORM. No web framework. SQLite is stdlib.

---

## Development Workflow

```bash
# Run tests
.venv/bin/pytest tests/ -v

# Run with coverage
.venv/bin/pytest tests/ --cov=core --cov=broker --cov=db --cov-report=term-missing

# Start scheduler (paper mode safe)
python main.py

# Inspect decisions
sqlite3 portfolio.db "SELECT ticker, date, score, model_confidence, self_critique FROM decisions ORDER BY date DESC LIMIT 20;"

# Find recurring data gaps across months (actionable for pipeline improvement)
sqlite3 portfolio.db "SELECT data_gaps, COUNT(*) FROM decisions GROUP BY data_gaps ORDER BY COUNT(*) DESC;"
```

### Environment setup

```bash
cp .env.template .env   # fill in OPENROUTER_API_KEY, EMAIL_*, IBKR_*, GITHUB_TOKEN, DASHBOARD_*
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Required `.env` keys:

| Key                       | Purpose                                                                             |
| ------------------------- | ----------------------------------------------------------------------------------- |
| `OPENROUTER_API_KEY`      | Monthly LLM analysis via OpenRouter (~$0.02/month)                                  |
| `GITHUB_TOKEN`            | Weekly veto pass via GitHub Copilot (free, fine-grained PAT with Models: Read-only) |
| `EMAIL_*`                 | SMTP credentials for weekly/monthly email reports                                   |
| `DASHBOARD_URL`           | Full URL of the dashboard (e.g. `https://warbuf.duckdns.org`)                       |
| `DASHBOARD_USER`          | Caddy basic_auth username                                                           |
| `DASHBOARD_PASSWORD_HASH` | Caddy bcrypt hash — generate with `caddy hash-password --plaintext 'yourpass'`      |
| `IBKR_*`                  | IBKR gateway credentials (only needed for live trading)                             |
| `EUR_USD_RATE`            | Override exchange rate (optional; default from `rules.yaml`)                        |

---

## Coding Standards (enforced — not suggestions)

### Code shape

- Write the simplest code that solves the problem. No speculative abstractions.
- Functions do one thing. If a function needs a comment to explain what it does, rename it.
- Prefer pure functions. Isolate side effects at the boundary.
- No code duplication. Extract only when used 3+ times (rule of three).
- Fail fast: validate inputs at the top, return early, avoid deep nesting.
- No magic numbers or strings. Use named constants.
- Delete dead code. Don't comment it out.

### Security (non-negotiable)

- Never hardcode secrets, credentials, or API keys.
- Sanitize all external inputs. Treat user input as hostile.
- Flag any use of `eval()`, shell injection patterns, or dynamic SQL.
- All SQL uses parameterized queries (`?` placeholders). No string formatting into SQL.

### Dependencies

- Do not introduce a dependency solvable in under 20 lines of stdlib.
- If a library is added, document the reason and the alternative considered in this file.

### Error handling

- Handle errors explicitly. Never swallow exceptions silently.
- Error messages must state what failed and where ("LLM call failed for AAPL in analyse_candidates").
- Distinguish recoverable errors (return/handle) from fatal ones (raise and let it crash).

### Naming

- Names reveal intent, not type. `is_risk_on` not `risk_flag`.
- Booleans: `is_`, `has_`, `can_` prefixes.
- Avoid abbreviations except: `id`, `url`, `http`, `db`, `qty`, `pct`.

### Tests

- New logic = new test. No exceptions.
- Test behavior, not implementation. Tests survive refactors.
- **One assertion per test.** AAA: Arrange / Act / Assert.
- All LLM calls mocked in tests — no real API calls in CI.

---

## Agentic Behaviour (for AI agents working on this repo)

> **Always check `GOAL.md` before implementing anything.** The goal is a realistic
> paper trading simulation (May 4 → June 4) before deploying real money on IBKR.
> Any change that makes paper execution less realistic (fractional shares, no slippage,
> ignoring fees, bypassing the cash floor) directly undermines the project goal.

### Implementation discipline

- Implement only what was asked. Do not add features, refactor unrelated code, or make
  "improvements" beyond the request scope.
- Do not add docstrings, comments, or type annotations to code you didn't change.
- Do not add error handling for scenarios that cannot happen. Validate only at system
  boundaries (user input, external APIs, DB reads).
- Do not create helpers or abstractions for one-time operations.
- Read a file before modifying it. Understand existing code before suggesting changes.

### Operational safety

Take local, reversible actions freely (editing files, running tests).
For actions that are hard to reverse or affect shared systems, **ask the user first**:

| Requires confirmation before acting |
| ----------------------------------- |
| Deleting files or branches          |
| Dropping database tables            |
| `rm -rf`, `git push --force`        |
| `git reset --hard`                  |
| Amending published commits          |
| Pushing code to remote              |
| Commenting on PRs / issues          |
| Modifying shared infrastructure     |

Do not use destructive actions as shortcuts. Do not bypass safety checks (e.g. `--no-verify`).
Do not discard unfamiliar files that may be in-progress work.

### Task tracking

Use a todo list for multi-step work:

1. Plan tasks — write the full list upfront.
2. Mark ONE item **in-progress** before starting it.
3. Mark it **completed** immediately after finishing.
4. Move to the next item.

Skip task tracking for single, trivial operations.

### Parallelization

- Batch independent **read-only** tool calls together (file reads, searches).
- Never run terminal commands in parallel — run one, wait for output, then run the next.
- Stop searching once you have enough context to act. Do not over-explore.
- If two separate searches return overlapping results, you have sufficient context.

### When blocked

- After two failed attempts with the same approach, step back and try a different strategy.
- Do not brute-force a blocked path. Consider alternative approaches or ask the user.

---

## What Is Not Yet Done

| Item                       | Notes                                                                                                                                                           |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| IBKR gateway Docker image  | Use `ghcr.io/extrange/ibkr-cp-gateway` or official IBKR image; needs manual login first                                                                         |
| Live trading               | Set `paper_mode: false` only after 30+ paper days and ~€3,000 capital; see Live Trading Readiness sidebar in dashboard                                          |
| Watchlist curation         | Review quarterly; currently 30 tickers in `rules.yaml`                                                                                                          |
| Nightly backup destination | `nightly_backup_job` writes to `./backups/` by default; set `BACKUP_DIR` env var to point to an external volume or Backblaze B2 mount for off-server durability |
| NYSE holidays (annual)     | Update `nyse_holidays` list in `rules.yaml` each December for the coming year                                                                                   |

---

## Deployment — Hetzner CX23 + Caddy (HTTPS)

**This is a cloud-hosted bot.** It runs 24/7 on a Hetzner CX23 (2 vCPU, 4 GB RAM, ~€4.49/month).
The scheduler wakes up every Monday at 09:00 Europe/Madrid and goes back to idle.
The Streamlit dashboard is always accessible at your domain over HTTPS.

`docker-compose.yml` runs three services: `caddy`, `dashboard`, and `warbuf`.
Caddy auto-issues a Let's Encrypt TLS certificate. No certbot, no nginx.

### Current live instance

| Property  | Value                                                                  |
| --------- | ---------------------------------------------------------------------- |
| Provider  | Hetzner CX23, Nuremberg                                                |
| IP        | `178.104.225.167`                                                      |
| Domain    | `warbuf.duckdns.org` (DuckDNS free subdomain)                          |
| Dashboard | `https://warbuf.duckdns.org` (Caddy basic_auth protected)              |
| Auth user | `warbuf` (password hash stored in `.env` as `DASHBOARD_PASSWORD_HASH`) |
| SSH key   | `~/.ssh/id_ed25519` (ed25519, added to Hetzner at creation)            |

### Caddy basic_auth setup

The dashboard is protected by HTTP Basic Auth configured in `Caddyfile`.
Credentials are passed via environment variables so the hash is never hardcoded.

```bash
# Generate bcrypt hash for a new password
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'yourpassword'

# Add to .env:
DASHBOARD_USER=warbuf
DASHBOARD_PASSWORD_HASH=$2a$14$...
```

`docker-compose.yml` passes `.env` to the caddy service via `env_file: .env`.
Restart caddy after changing the hash: `docker compose restart caddy`.

### DuckDNS domain setup (free)

If you need to recreate or change the domain:

1. Go to `https://www.duckdns.org` and sign in
2. Create subdomain → point to server IP
3. Update `Caddyfile`: replace hostname with `yoursubdomain.duckdns.org`
4. Update `.env`: `DASHBOARD_URL=https://yoursubdomain.duckdns.org`
5. Restart stack: `docker compose up -d`

### One-time server setup (Hetzner CX23)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Clone and configure
git clone https://github.com/Tgedin/WarBuf.git && cd WarBuf
cp .env.template .env
nano .env          # fill in OPENROUTER_API_KEY, GITHUB_TOKEN, EMAIL_*, DASHBOARD_*, IBKR_*

# 3. Update Caddyfile with real domain
sed -i 's/your-domain.com/warbuf.duckdns.org/' Caddyfile

# 4. Update DASHBOARD_URL in .env
sed -i 's|DASHBOARD_URL=.*|DASHBOARD_URL=https://warbuf.duckdns.org|' .env

# 5. Launch everything
docker compose up -d

# 6. Access dashboard at https://warbuf.duckdns.org
```

### Architecture diagram

```
Internet → Caddy :443 (auto-TLS, basic_auth) → dashboard:8501  (internal network)
                                              ← warbuf (scheduler, internal only)
                                                 both share warbuf_data volume (portfolio.db)
```

### Local development run (no domain needed)

```bash
# Run dashboard standalone (reads local portfolio.db)
PORTFOLIO_DB_PATH=demo.db streamlit run dashboard.py --server.headless true
# Access at http://localhost:8501
# Deep link to a tab: http://localhost:8501?page=Decisions

# Or run the full stack locally
docker compose up -d dashboard warbuf
```

### Deploying changes to the server

```bash
# Push code then SSH to rebuild
ssh root@178.104.225.167 'cd /root/WarBuf && git pull && docker compose up -d --build'

# Push updated .env only (no rebuild needed)
scp .env root@178.104.225.167:/root/WarBuf/.env
ssh root@178.104.225.167 'docker compose -f /root/WarBuf/docker-compose.yml restart warbuf'
```

---

## CI/CD Workflow (GitHub Actions → Hetzner)

Development follows a **feature branch → PR → merge → auto-deploy** cycle.
No code reaches the server without passing tests first.

### Branch model

```
main        ← production; every commit here is auto-deployed to Hetzner
dev         ← integration branch; PRs merge here first
feature/*   ← one branch per feature or fix; short-lived
hotfix/*    ← for urgent production fixes; merge directly to main + backport to dev
```

### CI pipeline (GitHub Actions — free for public repos, 2000 min/month for private)

On every push / PR to `main` or `dev`:

1. **Lint** — `ruff check .` (zero-tolerance; fails the build)
2. **Tests** — `pytest tests/ --cov=core --cov=broker --cov=db --cov=reporter --cov-fail-under=85`
3. **Security scan** — `pip-audit` (checks all dependencies against CVE database)
4. **Build Docker image** — `docker build .` (confirms the image builds cleanly)

Only after all four pass does a PR become mergeable.

### CD pipeline (auto-deploy to Hetzner on merge to `main`)

Add a `deploy` job to `ci.yml` that SSHes into the server and runs:

```bash
cd /home/warbuf && git pull && docker compose up -d --build
```

Credentials live in GitHub Actions Secrets. **Add these three secrets** in `github.com/<you>/WarBuf/settings/secrets/actions`:

| Secret name       | Value                                    |
| ----------------- | ---------------------------------------- |
| `HETZNER_HOST`    | Server IP address                        |
| `HETZNER_USER`    | SSH login user (e.g. `root` or `warbuf`) |
| `HETZNER_SSH_KEY` | Private key content (the full PEM block) |

Zero-downtime: the scheduler only runs at 09:00 Monday — deploy any other time without risk.

### Free toolchain

| Tool                     | Role                              | Free tier                                    |
| ------------------------ | --------------------------------- | -------------------------------------------- |
| GitHub Actions           | CI/CD runner                      | 2000 min/month (private), unlimited (public) |
| `ruff`                   | Python linter + formatter         | Free / open source                           |
| `pip-audit`              | Dependency CVE scanner            | Free / open source                           |
| `pytest-cov`             | Coverage enforcement              | Free / open source                           |
| GitHub branch protection | Block merges without passing CI   | Free on all plans                            |
| GitHub Secrets           | Store SSH key + env vars securely | Free on all plans                            |
| Dependabot               | Auto-PR for dependency updates    | Free on all plans                            |

### Security hardening checklist (before going live)

- [ ] Hetzner firewall: allow only ports 80, 443, and 22 (restrict 22 to your IP)
- [ ] SSH key-only auth on the server; disable password login
- [ ] `.env` never committed — verified by `.gitignore` + `gitleaks` pre-commit hook
- [ ] `pip-audit` in CI — fails build on any HIGH/CRITICAL CVE in dependencies
- [ ] GitHub branch protection on `main`: require PR + CI pass before merge
- [ ] Dependabot enabled: auto-PRs for dependency updates weekly
- [ ] `IBKR_*` credentials rotated after every paper phase ends

---

## IBKR Client Portal Web API — Behaviour Reference

`broker/ibkr.py` talks to the IBKR CP Gateway (a local Java process / Docker container).
Source: https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/

### Critical protocol facts

| Fact                 | Detail                                                                                                                           |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Session keepalive    | `GET /tickle` — call before every batch of API requests; session expires without it                                              |
| Session status       | `GET /iserver/auth/status` — check `authenticated` and `competing` before trading                                                |
| `competing: true`    | Another session (e.g. TWS) is active for the same username — must close it first                                                 |
| Portfolio pre-call   | `GET /portfolio/accounts` must be called once before any `/portfolio/*` endpoint                                                 |
| Order field          | Use `quantity` (share count), NOT `cashQty`. We compute qty = notional / yfinance_price                                          |
| Order reply messages | Submissions may return a confirmation request instead of an order_id; must POST `/iserver/reply/{id}` with `{"confirmed": true}` |
| Suppress messages    | POST `/iserver/questions/suppress` with `{"messageIds": [...]}` once per session to skip fat-finger prompts for automated orders |
| Rate limit           | 10 req/s global; `/iserver/orders` GET: 1 req/5s; `/tickle`: 1 req/s                                                             |
| Daily IServer reset  | ~01:00 local time on weeknights — no impact for Monday 09:00 schedule                                                            |
| SSL cert             | Gateway uses a self-signed cert; `verify=False` only for `localhost` connections                                                 |

### Order flow in `place_order()`

```
_tickle()                            ← keep session alive
_ensure_brokerage_session()          ← check authenticated, not competing
_suppress_order_reply_messages()     ← suppress fat-finger prompts (once)
_resolve_conid(ticker)               ← ticker → IBKR conid
get_last_price(ticker)               ← yfinance price for qty calculation
POST /iserver/account/{id}/orders    ← submit MKT DAY order with quantity
_confirm_order_replies(response)     ← loop: confirm any reply messages
return OrderResult                   ← order_id, filled_price, qty, fees
```

### Gateway setup

The IBKR CP Gateway is a Java process. IBKR provides a Docker image:

```bash
# Run gateway (requires IBKR credentials interactively first)
docker run -p 5000:5000 ghcr.io/extrange/ibkr-cp-gateway:latest
# Then authenticate at https://localhost:5000
```

See: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/
