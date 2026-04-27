"""
WarBuf dashboard — Streamlit.

Run locally:
    streamlit run dashboard.py

Via Docker:
    Accessible at http://localhost:8501 (or the domain Caddy proxies to).

Reads portfolio.db directly (read-only).  No live scoring — data is
what the bot has already recorded.  All amounts in EUR.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
import yfinance as yf

DB_PATH    = Path(os.getenv("PORTFOLIO_DB_PATH", "portfolio.db"))
RULES_PATH = Path(os.getenv("RULES_PATH", "rules.yaml"))

st.set_page_config(
    page_title="WarBuf",
    page_icon="📈",
    layout="wide",
)

_PAGES = ["Portfolio", "Weekly Report", "Trades", "Decisions", "Performance", "Forecasts"]

def _page_index() -> int:
    requested = st.query_params.get("page", "Portfolio")
    try:
        return _PAGES.index(requested)
    except ValueError:
        return 0

# ── DB helpers ────────────────────────────────────────────────────────────────

def _query(sql: str, params: tuple = ()) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame()


def _is_paper_mode() -> bool:
    """True when rules.yaml has paper_mode: true OR the DB only contains PAPER- trades."""
    if RULES_PATH.exists():
        try:
            rules = yaml.safe_load(RULES_PATH.read_text())
            return bool(rules.get("paper_mode", True))
        except Exception:
            pass
    # Fallback: infer from the trades table
    df = _query("SELECT ibkr_order_id FROM trades LIMIT 20")
    if df.empty:
        return True  # no trades yet — assume paper
    return df["ibkr_order_id"].str.startswith("PAPER-").all()


def _get_eur_usd_rate() -> float:
    if RULES_PATH.exists():
        try:
            rules = yaml.safe_load(RULES_PATH.read_text())
            return float(rules.get("eur_usd_rate", 1.08))
        except Exception:
            pass
    return 1.08


def _get_cash_eur() -> float | None:
    """Read live cash balance from portfolio_cash singleton row."""
    df = _query("SELECT balance_eur FROM portfolio_cash WHERE id = 1")
    if df.empty or df["balance_eur"].isna().all():
        return None
    return float(df["balance_eur"].iloc[0])


@st.cache_data(ttl=3600)
def _get_current_prices_eur(tickers: tuple[str, ...], eur_usd_rate: float) -> dict[str, float | None]:
    """Fetch current USD prices for held tickers and convert to EUR. 1h cache."""
    result: dict[str, float | None] = {}
    for ticker in tickers:
        try:
            price_usd = yf.Ticker(ticker).fast_info.last_price
            result[ticker] = float(price_usd) / eur_usd_rate if price_usd else None
        except Exception:
            result[ticker] = None
    return result


@st.cache_data(ttl=3600)
def _get_macro_regime() -> tuple[str, float, float]:
    """Returns (status, spy_current, spy_sma200). 1h cache."""
    try:
        hist = yf.Ticker("SPY").history(period="210d")
        if len(hist) < 200:
            return ("UNKNOWN", 0.0, 0.0)
        current = float(hist["Close"].iloc[-1])
        sma     = float(hist["Close"].tail(200).mean())
        return ("RISK ON" if current >= sma else "RISK OFF", current, sma)
    except Exception:
        return ("UNKNOWN", 0.0, 0.0)


# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.title("WarBuf")
st.sidebar.caption("Long-term investment bot")
page = st.sidebar.radio(
    "View",
    _PAGES,
    index=_page_index(),
)

# ── Paper mode banner (shown on every page) ───────────────────────────────────

if _is_paper_mode():
    st.sidebar.divider()
    st.sidebar.warning("🧪 **PAPER MODE**\n\nAll data is simulated. No real orders have been placed.", icon=None)
    with st.sidebar.expander("🔒 Live Trading Readiness"):
        df_days = _query("SELECT COUNT(DISTINCT DATE(date)) AS days FROM trades WHERE ibkr_order_id LIKE 'PAPER-%'")
        paper_days = int(df_days["days"].iloc[0]) if not df_days.empty and not df_days["days"].isna().all() else 0
        st.write(f"{'✅' if paper_days >= 30 else '⏳'} Paper days logged: **{paper_days}/30**")
        st.write("☐ Capital ≥ €3,000 (manual check)")
        st.write("☐ IB Gateway Docker container running")
        st.write("☐ Gateway authenticated at `localhost:5000`")
        st.write("☐ No competing IB session (TWS closed)")
        st.write("☐ Set `paper_mode: false` in `rules.yaml`")

st.sidebar.divider()
_regime, _spy_cur, _spy_sma = _get_macro_regime()
if _regime == "RISK ON":
    st.sidebar.success(f"📈 **{_regime}**\n\nSPY ${_spy_cur:.0f} > SMA ${_spy_sma:.0f}")
elif _regime == "RISK OFF":
    st.sidebar.error(f"🔴 **{_regime}**\n\nSPY ${_spy_cur:.0f} < SMA ${_spy_sma:.0f}")
else:
    st.sidebar.caption("Macro: data unavailable")


if page == "Portfolio":
    st.title("Current Positions")
    df = _query("""
        SELECT
            ticker,
            qty,
            ROUND(avg_cost_basis_eur, 4)  AS avg_cost_eur,
            ROUND(total_fees_eur, 4)       AS fees_eur,
            ROUND(qty * avg_cost_basis_eur, 2) AS total_cost_eur
        FROM positions
        ORDER BY total_cost_eur DESC
    """)
    if df.empty:
        st.info("No positions yet.")
    else:
        eur_usd = _get_eur_usd_rate()
        prices  = _get_current_prices_eur(tuple(df["ticker"].tolist()), eur_usd)

        total_cost  = df["total_cost_eur"].sum()
        total_value = 0.0

        for _, row in df.iterrows():
            ticker    = row["ticker"]
            qty       = row["qty"]
            cost      = row["total_cost_eur"]
            fees      = row["fees_eur"]
            price_eur = prices.get(ticker)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Ticker", ticker)
            c2.metric("Qty", f"{qty:.4f}")
            c3.metric("Cost basis", f"€{cost:,.2f}")
            if price_eur is not None:
                cur_val  = qty * price_eur
                gross    = cur_val - cost
                gain_pct = gross / cost * 100 if cost else 0.0
                net_gain = gross - fees
                total_value += cur_val
                c4.metric(
                    "Current value",
                    f"€{cur_val:,.2f}",
                    delta=f"€{gross:+,.2f} ({gain_pct:+.1f}%)  net €{net_gain:+,.2f}",
                )
            else:
                c4.metric("Current value", "—")
            st.divider()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total cost basis", f"\u20ac{total_cost:,.2f}")
        if total_value > 0:
            total_gain     = total_value - total_cost
            total_gain_pct = total_gain / total_cost * 100 if total_cost else 0.0
            c2.metric(
                "Total live value",
                f"\u20ac{total_value:,.2f}",
                delta=f"\u20ac{total_gain:+,.2f} ({total_gain_pct:+.1f}%)",
            )
        else:
            c2.metric("Total live value", "\u2014")
        cash_eur = _get_cash_eur()
        c3.metric("Cash", f"\u20ac{cash_eur:,.2f}" if cash_eur is not None else "\u2014")
        c4.metric("EUR/USD rate", f"{eur_usd:.4f}")

# ── Weekly Report ─────────────────────────────────────────────────────────────

elif page == "Weekly Report":
    st.title("Weekly Report — What Changed")

    # ── Portfolio WoW header ──────────────────────────────────────────────────
    perf = _query("""
        SELECT date, portfolio_value_eur, benchmark_value, cash_eur
        FROM performance
        ORDER BY date DESC
        LIMIT 2
    """)
    if not perf.empty:
        latest = perf.iloc[0]
        col1, col2, col3 = st.columns(3)
        col1.metric("Portfolio (€)", f"€{latest['portfolio_value_eur']:,.0f}")
        col2.metric("SPY", f"${latest['benchmark_value']:.2f}")
        col3.metric("Cash (€)", f"€{latest['cash_eur']:,.0f}")
        if len(perf) == 2:
            prev = perf.iloc[1]
            if prev["portfolio_value_eur"]:
                port_chg = (latest["portfolio_value_eur"] - prev["portfolio_value_eur"]) / prev["portfolio_value_eur"] * 100
                spy_chg  = (latest["benchmark_value"]     - prev["benchmark_value"])     / prev["benchmark_value"]     * 100
                col1.caption(f"WoW {'+' if port_chg >= 0 else ''}{port_chg:.1f}%")
                col2.caption(f"WoW {'+' if spy_chg  >= 0 else ''}{spy_chg:.1f}%")

    st.divider()

    # ── Score movements since last analysis ───────────────────────────────────
    st.subheader("Score movements")
    all_dec = _query("""
        SELECT ticker, date, ROUND(score, 3) AS score, action, model_confidence, vetoed
        FROM decisions
        ORDER BY date DESC
        LIMIT 200
    """)
    if all_dec.empty:
        st.info("No decisions recorded yet.")
    else:
        last2   = all_dec.groupby("ticker").head(2).reset_index(drop=True)
        has_two = last2.groupby("ticker").filter(lambda g: len(g) == 2)["ticker"].unique()
        rows = []
        for ticker in has_two:
            pair = last2[last2["ticker"] == ticker].sort_values("date", ascending=False)
            curr, prev = pair.iloc[0], pair.iloc[1]
            delta = round(float(curr["score"]) - float(prev["score"]), 3)
            rows.append({
                "ticker":     ticker,
                "prev_score": prev["score"],
                "curr_score": curr["score"],
                "Δ score":    f"{delta:+.3f}",
                "confidence": curr["model_confidence"],
                "vetoed":     bool(curr["vetoed"]),
                "date":       curr["date"],
            })
        if rows:
            st.dataframe(
                pd.DataFrame(rows).sort_values("Δ score", ascending=False),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("Need at least 2 analyses per ticker to show score movements.")

    st.divider()

    # ── Active vetoes ─────────────────────────────────────────────────────────
    st.subheader("Active vetoes")
    vetoes_df = _query("""
        SELECT d.ticker, d.date, d.veto_reason, ROUND(d.score, 3) AS score
        FROM decisions d
        INNER JOIN (
            SELECT ticker, MAX(date) AS max_date FROM decisions GROUP BY ticker
        ) latest ON d.ticker = latest.ticker AND d.date = latest.max_date
        WHERE d.vetoed = 1
        ORDER BY d.date DESC
    """)
    if vetoes_df.empty:
        st.success("No active vetoes.")
    else:
        for _, row in vetoes_df.iterrows():
            st.error(f"🚫 **{row['ticker']}** ({row['date']}) — {row['veto_reason']}")

    st.divider()

    # ── Recent trades ─────────────────────────────────────────────────────────
    st.subheader("Recent trades")
    trades_df = _query("""
        SELECT date, ticker, side,
               ROUND(qty, 4) AS qty,
               ROUND(price_eur, 2) AS price_eur,
               ROUND(fees_usd, 4) AS fees_usd,
               ibkr_order_id
        FROM trades
        ORDER BY date DESC
        LIMIT 20
    """)
    if trades_df.empty:
        st.info("No trades yet.")
    else:
        trades_df["mode"] = trades_df["ibkr_order_id"].apply(
            lambda oid: "🧪 paper" if str(oid).startswith("PAPER-") else "🟢 live"
        )
        st.dataframe(
            trades_df.drop(columns=["ibkr_order_id"]),
            width="stretch",
            hide_index=True,
        )

# ── Trades ────────────────────────────────────────────────────────────────────

elif page == "Trades":
    st.title("Trade History")
    df = _query("""
        SELECT
            date,
            ticker,
            side,
            ROUND(qty, 4)          AS qty,
            ROUND(price_usd, 4)    AS price_usd,
            ROUND(price_eur, 4)    AS price_eur,
            ROUND(eur_usd_rate, 4) AS eur_usd,
            ROUND(fees_usd, 4)     AS fees_usd,
            ibkr_order_id
        FROM trades
        ORDER BY date DESC
    """)
    if df.empty:
        st.info("No trades yet.")
    else:
        col1, col2 = st.columns(2)
        col1.metric("Total trades", len(df))
        col2.metric(
            "Total fees (€)",
            f"€{(df['fees_usd'] / df['eur_usd']).sum():,.4f}",
        )
        # Tag paper vs live trades visually
        df["mode"] = df["ibkr_order_id"].apply(
            lambda oid: "🧪 paper" if str(oid).startswith("PAPER-") else "🟢 live"
        )
        st.dataframe(df, width="stretch", hide_index=True)

# ── Decisions ─────────────────────────────────────────────────────────────────

elif page == "Decisions":
    st.title("Scoring Decisions")
    df = _query("""
        SELECT
            date,
            ticker,
            action,
            ROUND(score, 4)  AS score,
            model_confidence,
            vetoed,
            veto_reason,
            bull_case,
            bear_case,
            self_critique,
            algorithm_feedback,
            data_gaps,
            data_request
        FROM decisions
        ORDER BY date DESC
        LIMIT 200
    """)
    if df.empty:
        st.info("No decisions yet.")
    else:
        ticker_filter = st.selectbox(
            "Filter by ticker", ["All"] + sorted(df["ticker"].unique().tolist())
        )

        # Build score history from full dataset before filter for trend display
        score_history: dict[str, list[float]] = (
            df.groupby("ticker")["score"]
            .apply(list)
            .to_dict()
        )

        if ticker_filter != "All":
            df = df[df["ticker"] == ticker_filter]

        for _, row in df.iterrows():
            ticker      = row["ticker"]
            veto_marker = "🚫 " if row["vetoed"] else ""
            conf_emoji  = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
                str(row["model_confidence"]).lower(), "⚪"
            )
            history   = score_history.get(ticker, [])
            trend_str = " → ".join(f"{s:.3f}" for s in reversed(history[:5]))
            label = (
                f"{veto_marker}{ticker}  {row['date']}  "
                f"{conf_emoji} {row['model_confidence']}  "
                f"score {row['score']}  |  trend: {trend_str}"
            )
            with st.expander(label):
                if row["vetoed"] and pd.notna(row["veto_reason"]) and row["veto_reason"]:
                    st.error(f"**Veto:** {row['veto_reason']}")
                c1, c2 = st.columns(2)
                if pd.notna(row["bull_case"]) and row["bull_case"]:
                    c1.markdown(f"**Bull case**\n\n{row['bull_case']}")
                if pd.notna(row["bear_case"]) and row["bear_case"]:
                    c2.markdown(f"**Bear case**\n\n{row['bear_case']}")
                if pd.notna(row["self_critique"]) and row["self_critique"]:
                    st.warning(f"**Self-critique:** {row['self_critique']}")
                if pd.notna(row["algorithm_feedback"]) and row["algorithm_feedback"]:
                    st.info(f"**Algorithm feedback:** {row['algorithm_feedback']}")
                if pd.notna(row["data_request"]) and row["data_request"]:
                    st.caption(f"📋 Data request: {row['data_request']}")
                if pd.notna(row["data_gaps"]) and row["data_gaps"] not in ("", "[]"):
                    st.caption(f"Data gaps: {row['data_gaps']}")

        st.divider()
        st.subheader("Recurring data gaps")
        gaps_df = _query("""
            SELECT data_gaps, COUNT(*) AS occurrences
            FROM decisions
            WHERE data_gaps IS NOT NULL AND data_gaps != '[]'
            GROUP BY data_gaps
            ORDER BY occurrences DESC
            LIMIT 20
        """)
        if not gaps_df.empty:
            st.dataframe(gaps_df, width="stretch", hide_index=True)

        st.subheader("Algorithm feedback themes")
        feedback_df = _query("""
            SELECT algorithm_feedback, COUNT(*) AS occurrences
            FROM decisions
            WHERE algorithm_feedback IS NOT NULL AND algorithm_feedback != ''
            GROUP BY algorithm_feedback
            ORDER BY occurrences DESC
            LIMIT 20
        """)
        if not feedback_df.empty:
            st.dataframe(feedback_df, width="stretch", hide_index=True)

# ── Performance ───────────────────────────────────────────────────────────────

elif page == "Performance":
    st.title("Weekly Performance vs SPY")
    df = _query("""
        SELECT
            date,
            ROUND(portfolio_value_eur, 2) AS portfolio_eur,
            ROUND(benchmark_value, 4)     AS spy_value,
            ROUND(cash_eur, 2)            AS cash_eur
        FROM performance
        ORDER BY date ASC
    """)
    if df.empty:
        st.info("No performance snapshots yet.")
    else:
        df["date"] = pd.to_datetime(df["date"])
        # Compute % change from first row so both series start at 0
        first_p = df["portfolio_eur"].iloc[0]
        first_s = df["spy_value"].iloc[0]
        df["portfolio_pct"] = ((df["portfolio_eur"] - first_p) / first_p * 100).round(2)
        df["spy_pct"]       = ((df["spy_value"]     - first_s) / first_s * 100).round(2)

        # Drawdown from rolling peak
        running_max        = df["portfolio_eur"].cummax()
        df["drawdown_pct"] = ((df["portfolio_eur"] - running_max) / running_max * 100).round(2)
        max_drawdown       = df["drawdown_pct"].min()

        # Header metrics
        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) >= 2 else None
        alpha  = df["portfolio_pct"].iloc[-1] - df["spy_pct"].iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Portfolio vs start",
            f"{df['portfolio_pct'].iloc[-1]:+.1f}%",
            delta=f"alpha {alpha:+.1f}% vs SPY",
        )
        c2.metric(
            "Max drawdown",
            f"{max_drawdown:.1f}%",
            delta=f"current {df['drawdown_pct'].iloc[-1]:.1f}%",
            delta_color="inverse",
        )
        if prev is not None and prev["portfolio_eur"]:
            wow = (latest["portfolio_eur"] - prev["portfolio_eur"]) / prev["portfolio_eur"] * 100
            c3.metric("WoW change", f"{wow:+.1f}%")

        st.subheader("Returns vs SPY")
        st.line_chart(df.set_index("date")[["portfolio_pct", "spy_pct"]])
        st.subheader("Drawdown from peak")
        st.line_chart(df.set_index("date")[["drawdown_pct"]])
        st.dataframe(df.sort_values("date", ascending=False), width="stretch", hide_index=True)

# ── Forecasts ────────────────────────────────────────────────────────────────

elif page == "Forecasts":
    st.title("Monthly Forecasts vs Actuals")
    df = _query("""
        SELECT
            month,
            ROUND(expected_low,  2) AS expected_low_pct,
            ROUND(expected_high, 2) AS expected_high_pct,
            ROUND(actual, 2)        AS actual_pct,
            ROUND(benchmark_actual, 2) AS spy_actual_pct,
            notes
        FROM forecasts
        ORDER BY month DESC
    """)
    if df.empty:
        st.info("No forecasts yet.")
    else:
        resolved = df[df["actual_pct"].notna()].copy()
        pending  = df[df["actual_pct"].isna()].copy()

        # ── Summary metrics ──────────────────────────────────────────────────
        if not resolved.empty:
            in_range = resolved.apply(
                lambda r: r["expected_low_pct"] <= r["actual_pct"] <= r["expected_high_pct"],
                axis=1,
            )
            beat_spy = (resolved["actual_pct"] > resolved["spy_actual_pct"].fillna(float("-inf")))
            c1, c2, c3 = st.columns(3)
            c1.metric("Forecasts resolved", len(resolved))
            c2.metric("In-range hit rate",  f"{in_range.mean() * 100:.0f}%")
            c3.metric("Beat SPY",           f"{beat_spy.sum()} / {beat_spy.count()}")
            st.divider()

        # ── Resolved months ──────────────────────────────────────────────────
        if not resolved.empty:
            st.subheader("Resolved forecasts")
            for _, row in resolved.iterrows():
                hit   = row["expected_low_pct"] <= row["actual_pct"] <= row["expected_high_pct"]
                label = f"{'✅' if hit else '❌'}  {row['month']}  |  " \
                        f"forecast {row['expected_low_pct']:+.1f}% – {row['expected_high_pct']:+.1f}%  |  " \
                        f"actual {row['actual_pct']:+.1f}%  |  " \
                        f"SPY {row['spy_actual_pct']:+.1f}%" if pd.notna(row["spy_actual_pct"]) else \
                        f"{'✅' if hit else '❌'}  {row['month']}  |  " \
                        f"forecast {row['expected_low_pct']:+.1f}% – {row['expected_high_pct']:+.1f}%  |  " \
                        f"actual {row['actual_pct']:+.1f}%"
                with st.expander(label):
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Expected range",
                                f"{row['expected_low_pct']:+.1f}% – {row['expected_high_pct']:+.1f}%")
                    col2.metric("Actual return",  f"{row['actual_pct']:+.1f}%",
                                delta=f"vs forecast mid {(row['actual_pct'] - (row['expected_low_pct'] + row['expected_high_pct']) / 2):+.1f}%")
                    if pd.notna(row["spy_actual_pct"]):
                        col3.metric("SPY return", f"{row['spy_actual_pct']:+.1f}%",
                                    delta=f"alpha {row['actual_pct'] - row['spy_actual_pct']:+.1f}%")
                    if pd.notna(row["notes"]) and row["notes"]:
                        st.caption(row["notes"])

        # ── Pending months ───────────────────────────────────────────────────
        if not pending.empty:
            st.subheader("Pending (actual not yet recorded)")
            for _, row in pending.iterrows():
                with st.expander(f"🕐  {row['month']}  |  "
                                 f"forecast {row['expected_low_pct']:+.1f}% – {row['expected_high_pct']:+.1f}%"):
                    col1, col2 = st.columns(2)
                    col1.metric("Expected low",  f"{row['expected_low_pct']:+.1f}%")
                    col2.metric("Expected high", f"{row['expected_high_pct']:+.1f}%")
                    if pd.notna(row["notes"]) and row["notes"]:
                        st.caption(row["notes"])
