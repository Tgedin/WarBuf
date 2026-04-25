# WarBuf

Autonomous long-term investment bot. Math scores. LLM analyses. Human controls `rules.yaml`.

---

## How it works

```mermaid
flowchart TD
    A([Monday 09:00]) --> B[Load rules.yaml]
    B --> C{First Monday\nof month?}

    C -->|No — weekly job| D[Re-score all positions\n+ watchlist tickers]
    D --> E{Sell trigger?}
    E -->|Stop-loss ≥ 15%| F[Sell all]
    E -->|Score collapse > 0.25| G[Trim 50%]
    E -->|No trigger| H[LLM veto check\non held positions]
    H --> I[Send weekly digest email]

    C -->|Yes — monthly job| J{SPY > 200d SMA?}
    J -->|No — macro guard| K[Skip buys]
    J -->|Yes| L[Tier 1 hard filters\nmarket cap · PE · growth · D/E]
    L --> M[Tier 2 factor scoring\nquality · value · momentum · profitability]
    M --> N[Tier 3 LLM analysis\nbull/bear · veto check · algorithm feedback]
    N --> O{Vetoed?}
    O -->|Yes| P[Skip ticker]
    O -->|No| Q[Place buy order\nvia broker]
    Q --> R[Record trade + EUR cost basis]
    R --> S[Send monthly forecast email]

    subgraph Idle jobs
        T([Sunday 22:00]) --> U[Pre-warm yfinance cache\nfor all 30 watchlist tickers]
        V([Daily 03:00 UTC]) --> W[Backup portfolio.db → backups/]
    end
```

---

## Stack

| Layer        | Tool                                                            |
| ------------ | --------------------------------------------------------------- |
| Scheduler    | APScheduler (weekly + monthly + idle jobs)                      |
| Scoring      | 4-factor composite — quality · value · momentum · profitability |
| LLM analysis | LiteLLM → GitHub Models (gpt-4o)                                |
| Broker       | Paper (default) or IBKR Web API                                 |
| Persistence  | SQLite — EUR-native accounting                                  |
| Dashboard    | Streamlit (always-on, HTTPS via Caddy)                          |
| Hosting      | Hetzner CX23 (~€4.49/month)                                     |
| CI/CD        | GitHub Actions — lint · tests · security scan · auto-deploy     |

---

## Quickstart

```bash
cp .env.template .env   # fill in EMAIL_*, GITHUB_TOKEN
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py          # starts scheduler (paper mode safe)
.venv/bin/streamlit run dashboard.py   # dashboard at http://localhost:8501
```

All tunable parameters (weights, filters, model, watchlist) live in `rules.yaml` — no code edits needed.

See [AGENTS.md](AGENTS.md) for full architecture and coding conventions.
