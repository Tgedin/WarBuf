"""
Email reporter: weekly digest + monthly forecast + forecast vs actual.

Plain text only — no HTML, no templates, no dependencies beyond stdlib.
Weekly target: ~10 lines, 60-second read.
Monthly target: full self-critical analysis with LLM reasoning per ticker.
"""
from __future__ import annotations

import os
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from typing import TYPE_CHECKING

from db import Database

if TYPE_CHECKING:
    from core.agent import AnalysisReport

_SEP_LONG  = "─" * 37


# ── Email transport ───────────────────────────────────────────────────────────

def _send(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = os.environ["EMAIL_FROM"]
    msg["To"]      = os.environ["EMAIL_TO"]
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        # Gmail app passwords are shown with spaces for readability; strip them.
        password = os.environ["EMAIL_PASSWORD"].replace(" ", "")
        smtp.login(os.environ["EMAIL_FROM"], password)
        smtp.send_message(msg)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _pct(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.1f}%"


# ── Public API ────────────────────────────────────────────────────────────────

def send_weekly_digest(
    db: Database,
    positions: list[dict],     # [{"ticker", "pct", "action", "score", optionals}]
    alerts: list[str],
    news: list[str],           # ["MSFT  Azure beats Q1 — thesis intact"]
    next_action: str,
    spy_price: float,
    spy_sma: float,
    portfolio_eur: float,
    mtd_pct: float,
    spy_mtd_pct: float,
    eur_usd_rate: float = 1.0,
    dashboard_url: str = "",
) -> None:
    week     = date.today().strftime("%b %d %Y")
    macro    = "RISK-ON ✓" if spy_price > spy_sma else "RISK-OFF ✗"
    alpha    = mtd_pct - spy_mtd_pct
    alert_summary = f"⚠ {len(alerts)} alert{'s' if len(alerts) != 1 else ''}: {alerts[0]}" if alerts else "No alerts"
    report_link   = f"\n→ Full report: {dashboard_url}" if dashboard_url else ""

    body = (
        f"WarBuf · Week of {week}  |  EUR/USD {eur_usd_rate:.4f}\n"
        f"Portfolio €{portfolio_eur:,.0f}  ·  {_pct(mtd_pct)} MTD  ·  SPY {_pct(spy_mtd_pct)}  ·  {_pct(alpha)} alpha\n"
        f"Macro {macro}  ·  SPY {spy_price:.1f}  /  200d SMA {spy_sma:.1f}\n"
        f"{alert_summary}"
        f"{report_link}"
    )

    _send(f"WarBuf · {week}", body)


def send_monthly_forecast(
    month_label: str,               # "May 2026"
    macro_regime: str,
    expected_low: float,
    expected_high: float,
    downside: float,
    key_risk: str,
    position_outlooks: list[dict],  # [{"ticker", "outlook", "note"}]
    planned_action: str,
    db: Database,
    reports: list[AnalysisReport] | None = None,  # LLM analysis per candidate
) -> None:
    outlook_lines = "\n".join(
        f"  {p['ticker']:<8} {p['outlook']:<12} {p['note']}"
        for p in position_outlooks
    )

    body = (
        f"{_SEP_LONG}\n"
        f"WarBuf · {month_label} Forecast\n"
        f"{_SEP_LONG}\n\n"
        f"MACRO REGIME   {macro_regime}\n\n"
        f"PORTFOLIO FORECAST\n"
        f"  Expected return ({month_label}):  {_pct(expected_low)} to {_pct(expected_high)}  (base case)\n"
        f"  Downside scenario:      {_pct(downside)}           (if macro turns)\n"
        f"  Key risk:               {key_risk}\n\n"
        f"POSITION OUTLOOK\n{outlook_lines}\n\n"
        f"ACTIONS PLANNED  {planned_action}"
    )

    if reports:
        body += "\n\n" + _format_analysis_block(reports)

    body += f"\n{_SEP_LONG}"

    _send(f"WarBuf · {month_label} Forecast", body)

    month_key = datetime.strptime(month_label, "%B %Y").strftime("%Y-%m")
    db.save_forecast(month_key, expected_low, expected_high)


def _format_analysis_block(reports: list[AnalysisReport]) -> str:
    """Format the LLM analysis section for the monthly email."""
    sep = "·" * 33
    sections: list[str] = ["LLM ANALYSIS (Tier 3)", sep]

    for r in reports:
        veto_line = (
            f"  VETO ⚠  {r.veto_reason}"
            if r.vetoed
            else f"  HOLD/BUY  confidence={r.model_confidence}"
        )
        sections += [
            f"\n{r.ticker}  [{r.model_confidence.upper()}]",
            veto_line,
            f"  Bull  {r.bull_case}",
            f"  Bear  {r.bear_case}",
            f"  Critique  {r.self_critique}",
        ]
        if r.data_gaps:
            sections.append(f"  Data gaps  {'; '.join(r.data_gaps)}")
        if r.data_request:
            sections.append(f"  Data request  {r.data_request}")

    # Aggregate data requests — pipeline improvement signal
    requests = [r.data_request for r in reports if r.data_request]
    if requests:
        sections += [f"\n{sep}", "DATA REQUESTS (for next month's pipeline)"]
        sections += [f"  [{r.ticker}] {r.data_request}" for r in reports if r.data_request]

    return "\n".join(sections)


def send_forecast_vs_actual(
    month_label: str,
    expected_low: float,
    expected_high: float,
    actual: float,
    benchmark_actual: float,
    miss_note: str,
    db: Database,
) -> None:
    month_key = datetime.strptime(month_label, "%B %Y").strftime("%Y-%m")
    db.update_forecast_actual(month_key, actual, benchmark_actual)

    in_range = expected_low <= actual <= expected_high
    alpha    = actual - benchmark_actual

    body = (
        f"FORECAST vs ACTUAL  ({month_label})\n"
        f"  Expected:   {_pct(expected_low)} to {_pct(expected_high)}\n"
        f"  Actual:     {_pct(actual):<12} {'✓ within range' if in_range else '✗ outside range'}\n"
        f"  SPY:        {_pct(benchmark_actual):<12} {_pct(alpha)} alpha\n\n"
        f"  Miss: {miss_note or 'None — model on target'}"
    )

    _send(f"WarBuf · {month_label} Forecast vs Actual", body)
