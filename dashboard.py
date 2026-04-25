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

DB_PATH    = Path(os.getenv("PORTFOLIO_DB_PATH", "portfolio.db"))
RULES_PATH = Path(os.getenv("RULES_PATH", "rules.yaml"))

st.set_page_config(
    page_title="WarBuf",
    page_icon="📈",
    layout="wide",
)

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


# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.title("WarBuf")
st.sidebar.caption("Long-term investment bot")
page = st.sidebar.radio(
    "View",
    ["Portfolio", "Weekly Report", "Trades", "Decisions", "Performance", "Forecasts"],
)

# ── Paper mode banner (shown on every page) ───────────────────────────────────

if _is_paper_mode():
    st.sidebar.divider()
    st.sidebar.warning("🧪 **PAPER MODE**\n\nAll data is simulated. No real orders have been placed.", icon=None)



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
        st.dataframe(df, width="stretch", hide_index=True)
        st.metric("Total cost basis (€)", f"€{df['total_cost_eur'].sum():,.2f}")

# ── Weekly Report ─────────────────────────────────────────────────────────────

elif page == "Weekly Report":
    import json as _json

    st.title("Weekly Report")

    # ── Macro & portfolio header ──────────────────────────────────────────────
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
        col2.metric("SPY value", f"{latest['benchmark_value']:.4f}")
        col3.metric("Cash (€)", f"€{latest['cash_eur']:,.0f}")
        if len(perf) == 2:
            prev = perf.iloc[1]
            port_chg = (latest["portfolio_value_eur"] - prev["portfolio_value_eur"]) / prev["portfolio_value_eur"] * 100
            spy_chg  = (latest["benchmark_value"]     - prev["benchmark_value"])     / prev["benchmark_value"]     * 100
            col1.caption(f"WoW {'+' if port_chg >= 0 else ''}{port_chg:.1f}%")
            col2.caption(f"WoW {'+' if spy_chg  >= 0 else ''}{spy_chg:.1f}%")

    st.divider()

    # ── Positions ─────────────────────────────────────────────────────────────
    st.subheader("Positions")
    pos_df = _query("""
        SELECT
            ticker,
            qty,
            ROUND(avg_cost_basis_eur, 2) AS avg_cost_eur,
            ROUND(qty * avg_cost_basis_eur, 2) AS total_cost_eur,
            ROUND(total_fees_eur, 2) AS fees_eur
        FROM positions
        ORDER BY total_cost_eur DESC
    """)
    if pos_df.empty:
        st.info("No positions yet.")
    else:
        st.dataframe(pos_df, width="stretch", hide_index=True)

    st.divider()

    # ── Latest LLM decisions (most recent per ticker) ─────────────────────────
    st.subheader("Latest Analysis")
    decisions_df = _query("""
        SELECT d.date, d.ticker, d.action, ROUND(d.score, 3) AS score,
               d.model_confidence, d.vetoed, d.veto_reason,
               d.bull_case, d.bear_case, d.self_critique, d.data_gaps
        FROM decisions d
        INNER JOIN (
            SELECT ticker, MAX(date) AS max_date
            FROM decisions
            GROUP BY ticker
        ) latest ON d.ticker = latest.ticker AND d.date = latest.max_date
        ORDER BY d.score DESC
    """)
    if decisions_df.empty:
        st.info("No decisions recorded yet.")
    else:
        for _, row in decisions_df.iterrows():
            vetoed = bool(row.get("vetoed", 0))
            conf   = row.get("model_confidence") or "—"
            score  = row.get("score")

            header_color = "🔴" if vetoed else ("🟢" if score and score >= 0.6 else "🟡")
            with st.expander(
                f"{header_color} **{row['ticker']}** · score {score:.3f} · {row['action']} · {conf} confidence · {row['date']}",
                expanded=False,
            ):
                if vetoed:
                    st.error(f"**VETOED** — {row.get('veto_reason') or 'no reason recorded'}")

                c1, c2 = st.columns(2)
                c1.markdown(f"**Bull case**\n\n{row.get('bull_case') or '—'}")
                c2.markdown(f"**Bear case**\n\n{row.get('bear_case') or '—'}")
                st.markdown(f"**Self-critique**\n\n{row.get('self_critique') or '—'}")

                raw_gaps = row.get("data_gaps")
                if raw_gaps and raw_gaps != "[]":
                    try:
                        gaps = _json.loads(raw_gaps)
                        if gaps:
                            st.caption("Data gaps: " + " · ".join(gaps))
                    except Exception:
                        pass

    st.divider()

    # ── Alerts: recent vetoes + any score collapses ───────────────────────────
    st.subheader("Alerts")
    alerts_df = _query("""
        SELECT date, ticker, veto_reason, score
        FROM decisions
        WHERE vetoed = 1
        ORDER BY date DESC
        LIMIT 10
    """)
    if alerts_df.empty:
        st.success("No active vetoes.")
    else:
        st.dataframe(alerts_df, width="stretch", hide_index=True)

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
        if ticker_filter != "All":
            df = df[df["ticker"] == ticker_filter]

        # Summary table — compact columns only
        summary_cols = ["date", "ticker", "action", "score", "model_confidence", "vetoed"]
        st.dataframe(df[summary_cols], width="stretch", hide_index=True)

        st.subheader("Decision detail")
        for _, row in df.iterrows():
            veto_marker = "🚫 " if row["vetoed"] else ""
            conf_emoji  = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
                str(row["model_confidence"]).lower(), "⚪"
            )
            label = f"{veto_marker}{row['ticker']}  {row['date']}  {conf_emoji} {row['model_confidence']}  score {row['score']}"
            with st.expander(label):
                if row["vetoed"] and pd.notna(row["veto_reason"]) and row["veto_reason"]:
                    st.error(f"**Veto:** {row['veto_reason']}")
                c1, c2 = st.columns(2)
                if pd.notna(row["bull_case"]) and row["bull_case"]:
                    c1.markdown(f"**Bull case**\n\n{row['bull_case']}")
                if pd.notna(row["bear_case"]) and row["bear_case"]:
                    c2.markdown(f"**Bear case**\n\n{row['bear_case']}")
                if pd.notna(row["self_critique"]) and row["self_critique"]:
                    st.caption(f"Self-critique: {row['self_critique']}")
                if pd.notna(row["algorithm_feedback"]) and row["algorithm_feedback"]:
                    st.info(f"**Algorithm feedback:** {row['algorithm_feedback']}")
                if pd.notna(row["data_request"]) and row["data_request"]:
                    st.caption(f"Data request: {row['data_request']}")
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
        st.line_chart(df.set_index("date")[["portfolio_pct", "spy_pct"]])
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
