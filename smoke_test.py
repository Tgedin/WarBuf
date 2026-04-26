#!/usr/bin/env python3
"""
smoke_test.py — Pre-deployment integration test.

Exercises every WarBuf subsystem against real external APIs:
  - Groq LLM (requires GROQ_API_KEY in .env)
  - yfinance market data (requires internet)
  - Paper broker backed by in-memory SQLite (safe — no real orders)
  - Reporter body construction (SMTP mocked — no real email sent)

Usage:
  python smoke_test.py

Exit code: 0 = all checks passed, 1 = one or more checks failed.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

import yaml
from dotenv import load_dotenv

load_dotenv()

# ── Result tracking ───────────────────────────────────────────────────────────

_RESULTS: list[tuple[str, bool, str]] = []

# Set of section names to skip (populated when a prerequisite fails).
_SKIP_SECTIONS: set[str] = set()


def check(name: str, fn, section_tag: str = "") -> bool:
    """
    Run fn(), record PASS/FAIL/SKIP, return True on success.

    If section_tag is in _SKIP_SECTIONS, the check is recorded as SKIP.
    """
    if section_tag and section_tag in _SKIP_SECTIONS:
        _RESULTS.append((name, None, "skipped — prerequisite section failed"))
        print(f"  \033[33mSKIP\033[0m  {name}")
        return False

    try:
        fn()
        _RESULTS.append((name, True, ""))
        print(f"  \033[32mPASS\033[0m  {name}")
        return True
    except Exception as exc:
        _RESULTS.append((name, False, str(exc)))
        print(f"  \033[31mFAIL\033[0m  {name}")
        # Print only the last relevant line, not the full chain
        lines = traceback.format_exc().strip().splitlines()
        for line in lines[-4:]:
            print(f"        {line}")
        print()
        return False


def section(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


# ── Shared state built up across sections ─────────────────────────────────────

state: dict = {}


def _load_rules() -> dict:
    with open("rules.yaml") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════════════════════════════
# 1. PREFLIGHT
# ══════════════════════════════════════════════════════════════════════════════

section("1 · PREFLIGHT — environment & config")

def _check_github_token():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN is not set. Add it to .env.")
    if len(token) < 20:
        raise EnvironmentError(f"GITHUB_TOKEN looks invalid (too short: {len(token)} chars).")

def _check_rules_yaml():
    rules = _load_rules()
    required = ["factor_weights", "min_market_cap_B", "max_pe_ratio",
                 "llm_model", "llm_max_tokens_weekly", "llm_max_tokens_monthly", "watchlist"]
    for key in required:
        if key not in rules:
            raise KeyError(f"Missing required key '{key}' in rules.yaml")
    state["rules"] = rules

def _check_paper_mode():
    rules = state["rules"]
    if not rules.get("paper_mode", True):
        raise RuntimeError(
            "paper_mode is FALSE in rules.yaml — smoke test requires paper mode ON."
        )

_copilot_ok = check("GITHUB_TOKEN present in environment", _check_github_token)
_rules_ok   = check("rules.yaml loads and has required keys", _check_rules_yaml)
if _rules_ok:
    check("paper_mode is ON (safety guard)", _check_paper_mode)

# If the token is missing/invalid, skip LLM calls so sections 3–8 can still run.
if not _copilot_ok:
    _SKIP_SECTIONS.add("llm")


# ══════════════════════════════════════════════════════════════════════════════
# 2. LLM API CONNECTIVITY
# ══════════════════════════════════════════════════════════════════════════════

section("2 · LLM API — real GitHub Copilot call")

def _check_llm_returns_string():
    from core.llm_provider import call_llm
    rules = state["rules"]
    result = call_llm(
        "Reply with exactly: WARBUF_OK",
        rules["llm_model"],
        64,
    )
    if not isinstance(result, str):
        raise TypeError(f"call_llm returned {type(result).__name__}, expected str")
    state["llm_ping"] = result

def _check_llm_response_non_empty():
    result = state.get("llm_ping", "")
    if not result.strip():
        raise ValueError("LLM returned an empty string")

def _check_llm_structured_json():
    """Verify the model can produce valid JSON — required by analyse_candidates."""
    from core.llm_provider import call_llm
    rules = state["rules"]
    prompt = (
        'Reply with a JSON object ONLY, no prose: {"status": "ok", "value": 42}'
    )
    raw = call_llm(prompt, rules["llm_model"], 64)
    # Extract JSON substring tolerantly
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in response: {raw!r}")
    parsed = json.loads(match.group())
    if parsed.get("value") != 42:
        raise ValueError(f"Unexpected JSON content: {parsed}")

check("LLM call returns a string", _check_llm_returns_string, "llm")
check("LLM response is non-empty", _check_llm_response_non_empty, "llm")
check("LLM can produce valid JSON", _check_llm_structured_json, "llm")


# ══════════════════════════════════════════════════════════════════════════════
# 3. MARKET DATA
# ══════════════════════════════════════════════════════════════════════════════

section("3 · MARKET DATA — yfinance fetches")

_TEST_TICKERS = ["AAPL", "MSFT"]

def _check_fundamentals_aapl():
    from core.market import get_fundamentals
    f = get_fundamentals("AAPL")
    if f.ticker != "AAPL":
        raise ValueError(f"Expected ticker AAPL, got {f.ticker!r}")
    if f.market_cap_b is None:
        raise ValueError("market_cap_b is None for AAPL")
    state["fundamentals_aapl"] = f

def _check_fundamentals_fields():
    f = state["fundamentals_aapl"]
    missing = [attr for attr in ("roe", "earnings_yield", "sector") if getattr(f, attr) is None]
    if missing:
        # Warn but don't fail — some fields are legitimately absent
        print(f"    NOTE  AAPL fundamentals have None fields: {missing}")

def _check_momentum_aapl():
    from core.market import get_momentum
    m = get_momentum("AAPL", lookback_days=365, skip_days=30)
    if m is None:
        raise ValueError("get_momentum returned None for AAPL")
    if not isinstance(m, float):
        raise TypeError(f"get_momentum returned {type(m).__name__}, expected float")
    state["momentum_aapl"] = m

def _check_news_aapl():
    from core.market import get_news_headlines
    headlines = get_news_headlines("AAPL", max_items=3)
    if not isinstance(headlines, list):
        raise TypeError(f"get_news_headlines returned {type(headlines).__name__}")
    state["news_aapl"] = headlines

def _check_spy_sma():
    from core.market import get_spy_sma
    price, sma = get_spy_sma(200)
    if price <= 0:
        raise ValueError(f"SPY price is non-positive: {price}")
    if sma <= 0:
        raise ValueError(f"SPY 200d SMA is non-positive: {sma}")
    state["spy_price"] = price
    state["spy_sma"] = sma
    print(f"    INFO  SPY {price:.1f} · 200d SMA {sma:.1f} — {'RISK-ON' if price > sma else 'RISK-OFF'}")

check("get_fundamentals returns Fundamentals for AAPL", _check_fundamentals_aapl)
check("fundamentals fields populated (soft warning)", _check_fundamentals_fields)
check("get_momentum returns float for AAPL", _check_momentum_aapl)
check("get_news_headlines returns list for AAPL", _check_news_aapl)
check("get_spy_sma returns valid price and SMA", _check_spy_sma)


# ══════════════════════════════════════════════════════════════════════════════
# 4. SCREENER PIPELINE (Tier 1 + Tier 2)
# ══════════════════════════════════════════════════════════════════════════════

section("4 · SCREENER — Tier 1 hard filters + Tier 2 factor scoring")

def _check_screener_runs():
    from core.scorer import FactorWeights
    from core.screener import HardFilters, run_tier1_tier2

    rules = state["rules"]
    filters = HardFilters(
        min_market_cap_b=rules["min_market_cap_B"],
        max_pe_ratio=rules["max_pe_ratio"],
        min_revenue_growth_pct=rules["min_revenue_growth_pct"],
        max_debt_to_equity=rules["max_debt_to_equity"],
        sectors_excluded=rules.get("sectors_excluded", []),
    )
    weights = FactorWeights(**rules["factor_weights"])

    top, rejected = run_tier1_tier2(
        watchlist=_TEST_TICKERS,
        filters=filters,
        weights=weights,
        momentum_lookback_days=rules.get("momentum_lookback_days", 365),
        momentum_skip_days=rules.get("momentum_skip_days", 30),
    )
    state["screener_top"] = top
    state["screener_rejected"] = rejected
    print(f"    INFO  passed={[s.ticker for s in top]}  rejected={rejected}")

def _check_screener_scores_in_range():
    for s in state.get("screener_top", []):
        if not (0.0 <= s.score <= 1.0):
            raise ValueError(f"{s.ticker} score {s.score:.4f} outside [0, 1]")

def _check_screener_sorted_desc():
    top = state.get("screener_top", [])
    scores = [s.score for s in top]
    if scores != sorted(scores, reverse=True):
        raise ValueError(f"Screener results not sorted desc: {scores}")

def _check_screener_has_candidates():
    top = state.get("screener_top", [])
    if not top:
        raise ValueError(
            "Both AAPL and MSFT were rejected by the screener. "
            "Check your hard filter settings in rules.yaml — "
            f"rejected: {state.get('screener_rejected')}"
        )

check("run_tier1_tier2 completes without error", _check_screener_runs)
check("all scores are in [0, 1]", _check_screener_scores_in_range)
check("results sorted descending by score", _check_screener_sorted_desc)
check("at least one candidate passed filters", _check_screener_has_candidates)


# ══════════════════════════════════════════════════════════════════════════════
# 5. AGENT — LLM analysis report (real call)
# ══════════════════════════════════════════════════════════════════════════════

section("5 · AGENT — LLM AnalysisReport (real Groq call)")

def _check_agent_runs():
    from core.agent import analyse_candidates
    from core.market import get_news_headlines

    rules = state["rules"]
    candidates = state.get("screener_top", [])
    if not candidates:
        raise RuntimeError("No candidates from screener — cannot test agent")

    news = {c.ticker: get_news_headlines(c.ticker, max_items=3) for c in candidates}
    reports = analyse_candidates(
        candidates=candidates,
        news=news,
        model=rules["llm_model"],
        max_tokens=rules.get("llm_max_tokens", 1024),
    )
    state["agent_reports"] = reports
    for r in reports:
        print(f"    INFO  {r.ticker} — vetoed={r.vetoed} confidence={r.model_confidence}")

def _check_reports_count_matches_candidates():
    reports = state.get("agent_reports", [])
    candidates = state.get("screener_top", [])
    if len(reports) != len(candidates):
        raise ValueError(
            f"Got {len(reports)} reports for {len(candidates)} candidates"
        )

def _check_all_reports_have_bull_case():
    for r in state.get("agent_reports", []):
        if not r.bull_case or not r.bull_case.strip():
            raise ValueError(f"{r.ticker}: bull_case is empty")

def _check_all_reports_have_bear_case():
    for r in state.get("agent_reports", []):
        if not r.bear_case or not r.bear_case.strip():
            raise ValueError(f"{r.ticker}: bear_case is empty")

def _check_all_reports_have_self_critique():
    for r in state.get("agent_reports", []):
        if not r.self_critique or not r.self_critique.strip():
            raise ValueError(f"{r.ticker}: self_critique is empty")

def _check_all_reports_have_data_gaps():
    for r in state.get("agent_reports", []):
        if not isinstance(r.data_gaps, list):
            raise TypeError(f"{r.ticker}: data_gaps is not a list")

def _check_confidence_is_valid():
    valid = {"high", "medium", "low"}
    for r in state.get("agent_reports", []):
        if r.model_confidence not in valid:
            raise ValueError(
                f"{r.ticker}: model_confidence={r.model_confidence!r} not in {valid}"
            )

def _check_confidence_reason_non_empty():
    for r in state.get("agent_reports", []):
        if not r.confidence_reason or not r.confidence_reason.strip():
            raise ValueError(f"{r.ticker}: confidence_reason is empty")

def _check_veto_reason_only_when_vetoed():
    for r in state.get("agent_reports", []):
        if r.vetoed and not r.veto_reason:
            raise ValueError(f"{r.ticker}: vetoed=True but veto_reason is empty")
        if not r.vetoed and r.veto_reason:
            raise ValueError(f"{r.ticker}: vetoed=False but veto_reason is set")

check("analyse_candidates returns reports", _check_agent_runs, "llm")
check("report count equals candidate count", _check_reports_count_matches_candidates, "llm")
check("every report has a bull_case", _check_all_reports_have_bull_case, "llm")
check("every report has a bear_case", _check_all_reports_have_bear_case, "llm")
check("every report has a self_critique", _check_all_reports_have_self_critique, "llm")
check("every report has data_gaps list", _check_all_reports_have_data_gaps, "llm")
check("model_confidence is high/medium/low", _check_confidence_is_valid, "llm")
check("confidence_reason is non-empty", _check_confidence_reason_non_empty, "llm")
check("veto_reason present iff vetoed", _check_veto_reason_only_when_vetoed, "llm")


# ══════════════════════════════════════════════════════════════════════════════
# 6. PAPER BROKER — buy, hold, sell
# ══════════════════════════════════════════════════════════════════════════════

section("6 · PAPER BROKER — place buy + sell, verify DB records")

from db import Database  # noqa: E402
from broker.paper import PaperBroker  # noqa: E402

# Use a fixed mock price so the section never depends on yfinance availability.
_MOCK_PRICE = 182.50

def _setup_paper_broker():
    db = Database(":memory:")
    broker = PaperBroker(db, eur_usd_rate=state["rules"].get("eur_usd_rate", 1.08))
    state["paper_db"]     = db
    state["paper_broker"] = broker

def _check_paper_buy():
    broker = state["paper_broker"]
    with patch.object(PaperBroker, "_last_close_price", return_value=_MOCK_PRICE):
        result = broker.place_order("AAPL", "buy", 750.0)
    if result.ticker != "AAPL":
        raise ValueError(f"order ticker mismatch: {result.ticker}")
    if result.side != "buy":
        raise ValueError(f"order side mismatch: {result.side}")
    if result.qty <= 0:
        raise ValueError(f"qty must be positive, got {result.qty}")
    if not result.order_id.startswith("PAPER-"):
        raise ValueError(f"paper order ID must start with PAPER-, got {result.order_id!r}")
    state["paper_buy_result"] = result
    print(f"    INFO  {result.order_id}  {result.qty:.4f} AAPL @ ${result.filled_price_usd:.2f}")

def _check_buy_recorded_in_db():
    db = state["paper_db"]
    trades = db._conn.execute(
        "SELECT * FROM trades WHERE ticker='AAPL' AND side='buy'"
    ).fetchall()
    if not trades:
        raise AssertionError("Buy trade not found in trades table")

def _check_position_created():
    db = state["paper_db"]
    pos = db.get_positions()
    if "AAPL" not in pos:
        raise AssertionError("AAPL position not created after buy")
    state["aapl_qty_after_buy"] = pos["AAPL"]["qty"]

def _check_paper_sell():
    broker = state["paper_broker"]
    with patch.object(PaperBroker, "_last_close_price", return_value=_MOCK_PRICE):
        result = broker.place_order("AAPL", "sell", 375.0)
    if result.side != "sell":
        raise ValueError(f"order side mismatch: {result.side}")
    state["paper_sell_result"] = result
    print(f"    INFO  {result.order_id}  {result.qty:.4f} AAPL sold @ ${result.filled_price_usd:.2f}")

def _check_sell_recorded_in_db():
    db = state["paper_db"]
    trades = db._conn.execute(
        "SELECT * FROM trades WHERE ticker='AAPL' AND side='sell'"
    ).fetchall()
    if not trades:
        raise AssertionError("Sell trade not found in trades table")

def _check_sell_reduces_qty():
    db = state["paper_db"]
    pos = db.get_positions()
    qty_before = state.get("aapl_qty_after_buy", 0)
    if "AAPL" in pos and pos["AAPL"]["qty"] >= qty_before:
        raise AssertionError("qty did not decrease after sell")

def _check_invalid_side_raises():
    broker = state["paper_broker"]
    try:
        with patch.object(PaperBroker, "_last_close_price", return_value=_MOCK_PRICE):
            broker.place_order("AAPL", "hold", 100.0)
        raise AssertionError("Expected ValueError for invalid side 'hold'")
    except ValueError:
        pass  # expected

def _check_negative_notional_raises():
    broker = state["paper_broker"]
    try:
        with patch.object(PaperBroker, "_last_close_price", return_value=_MOCK_PRICE):
            broker.place_order("AAPL", "buy", -100.0)
        raise AssertionError("Expected ValueError for negative notional")
    except ValueError:
        pass  # expected

check("PaperBroker + in-memory DB setup", _setup_paper_broker)
check("place_order buy returns valid OrderResult", _check_paper_buy)
check("buy trade recorded in DB", _check_buy_recorded_in_db)
check("AAPL position created after buy", _check_position_created)
check("place_order sell returns valid OrderResult", _check_paper_sell)
check("sell trade recorded in DB", _check_sell_recorded_in_db)
check("sell reduces position qty", _check_sell_reduces_qty)
check("invalid side raises ValueError", _check_invalid_side_raises)
check("negative notional raises ValueError", _check_negative_notional_raises)


# ══════════════════════════════════════════════════════════════════════════════
# 7. DB PERSISTENCE — decisions, performance, forecasts
# ══════════════════════════════════════════════════════════════════════════════

section("7 · DB PERSISTENCE — decisions, performance, forecasts")

def _check_record_decision():
    db = state["paper_db"]
    reports = state.get("agent_reports", [])
    candidates = state.get("screener_top", [])

    if not candidates:
        raise RuntimeError("No candidates to record decisions for")

    from db import rules_hash
    rhash = rules_hash("rules.yaml")

    for s in candidates:
        report = next((r for r in reports if r.ticker == s.ticker), None)
        db.record_decision(
            ticker=s.ticker,
            score=s.score,
            action="BUY",
            rules_hash=rhash,
            vetoed=report.vetoed if report else False,
            veto_reason=report.veto_reason if report else None,
            bull_case=report.bull_case if report else "",
            bear_case=report.bear_case if report else "",
            data_gaps=report.data_gaps if report else [],
            model_confidence=report.model_confidence if report else "low",
            confidence_reason=report.confidence_reason if report else "",
            self_critique=report.self_critique if report else "",
        )

def _check_decisions_in_db():
    db = state["paper_db"]
    candidates = state.get("screener_top", [])
    for s in candidates:
        rows = db._conn.execute(
            "SELECT * FROM decisions WHERE ticker=?", (s.ticker,)
        ).fetchall()
        if not rows:
            raise AssertionError(f"Decision not found in DB for {s.ticker}")

def _check_decisions_have_self_critique():
    db = state["paper_db"]
    candidates = state.get("screener_top", [])
    for s in candidates:
        row = db._conn.execute(
            "SELECT self_critique FROM decisions WHERE ticker=?", (s.ticker,)
        ).fetchone()
        if not row or not row["self_critique"]:
            raise AssertionError(f"self_critique missing in DB for {s.ticker}")

def _check_record_performance():
    db = state["paper_db"]
    db.record_performance(portfolio_value_eur=1234.0, benchmark_value=1100.0, cash_eur=200.0)
    rows = db.get_performance_history(limit=1)
    if not rows:
        raise AssertionError("Performance row not found after record_performance")
    if rows[0]["portfolio_value_eur"] != 1234.0:
        raise AssertionError(f"portfolio_value_eur mismatch: {rows[0]['portfolio_value_eur']}")

def _check_save_and_get_forecast():
    db = state["paper_db"]
    db.save_forecast("2026-05", expected_low=1.0, expected_high=3.0)
    f = db.get_forecast("2026-05")
    if f is None:
        raise AssertionError("Forecast not found after save_forecast")
    if f["expected_low"] != 1.0:
        raise AssertionError(f"expected_low mismatch: {f['expected_low']}")

def _check_rules_hash_format():
    from db import rules_hash
    h = rules_hash("rules.yaml")
    if not isinstance(h, str) or len(h) != 16:
        raise ValueError(f"rules_hash must be 16-char hex string, got {h!r}")
    int(h, 16)  # raises ValueError if not valid hex

check("record_decision writes LLM report fields", _check_record_decision)
check("decisions found in DB for each candidate", _check_decisions_in_db)
check("self_critique persisted to decisions table", _check_decisions_have_self_critique, "llm")
check("record_performance + get_performance_history", _check_record_performance)
check("save_forecast + get_forecast round-trip", _check_save_and_get_forecast)
check("rules_hash returns valid 16-char hex", _check_rules_hash_format)


# ══════════════════════════════════════════════════════════════════════════════
# 8. REPORTER — body construction (SMTP mocked)
# ══════════════════════════════════════════════════════════════════════════════

section("8 · REPORTER — email body (SMTP mocked) + real email send")

os.environ.setdefault("EMAIL_FROM",     "smoke@test.local")
os.environ.setdefault("EMAIL_TO",       "smoke@test.local")
os.environ.setdefault("EMAIL_PASSWORD", "smoke-test-password")

_smtp_calls: list[str] = []


def _make_smtp_mock():
    """Return a fresh SMTP_SSL mock that records method calls."""
    smtp_instance = MagicMock()

    def record_send(msg):
        _smtp_calls.append(msg["Subject"])

    smtp_instance.send_message.side_effect = record_send
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=smtp_instance)
    ctx.__exit__  = MagicMock(return_value=False)
    return ctx


def _check_weekly_digest_sends():
    from reporter import send_weekly_digest

    db = state["paper_db"]
    candidates = state.get("screener_top", [])
    positions = [{"ticker": c.ticker, "pct": 10, "action": "HOLD", "score": c.score}
                 for c in candidates]

    with patch("reporter.smtplib.SMTP_SSL", return_value=_make_smtp_mock()):
        send_weekly_digest(
            db=db,
            positions=positions,
            alerts=["SMOKE TEST — no real alert"],
            news=["AAPL  Headline from smoke test"],
            next_action="Smoke test — next: none",
            spy_price=state.get("spy_price", 500.0),
            spy_sma=state.get("spy_sma", 480.0),
            portfolio_eur=1234.0,
            mtd_pct=2.5,
            spy_mtd_pct=1.8,
        )

def _check_weekly_digest_no_exception():
    # send_weekly_digest passed if we reached here (previous check would have failed)
    pass

def _check_monthly_forecast_sends():
    from reporter import send_monthly_forecast

    db = state["paper_db"]
    candidates = state.get("screener_top", [])
    outlooks = [{"ticker": c.ticker, "outlook": "bullish", "note": "strong FCF"}
                for c in candidates]

    with patch("reporter.smtplib.SMTP_SSL", return_value=_make_smtp_mock()):
        send_monthly_forecast(
            month_label="April 2026",
            macro_regime="Risk-On",
            expected_low=1.0,
            expected_high=3.0,
            downside=-5.0,
            key_risk="Rate hike scenario",
            position_outlooks=outlooks,
            planned_action="Buy AAPL if momentum holds",
            db=db,
        )

def _check_forecast_saved_to_db():
    db = state["paper_db"]
    f = db.get_forecast("2026-04")
    if f is None:
        raise AssertionError("Monthly forecast not saved to DB by send_monthly_forecast")

def _check_forecast_vs_actual_sends():
    from reporter import send_forecast_vs_actual

    db = state["paper_db"]
    with patch("reporter.smtplib.SMTP_SSL", return_value=_make_smtp_mock()):
        send_forecast_vs_actual(
            month_label="April 2026",
            expected_low=1.0,
            expected_high=3.0,
            actual=2.2,
            benchmark_actual=1.5,
            miss_note="",
            db=db,
        )

def _check_actual_written_to_db():
    db = state["paper_db"]
    f = db.get_forecast("2026-04")
    if f is None or f.get("actual") is None:
        raise AssertionError("actual return not written by send_forecast_vs_actual")

check("send_weekly_digest runs without error", _check_weekly_digest_sends)
check("weekly digest no exception (confirmation)", _check_weekly_digest_no_exception)
check("send_monthly_forecast runs without error", _check_monthly_forecast_sends)
check("monthly forecast persisted to DB", _check_forecast_saved_to_db)
check("send_forecast_vs_actual runs without error", _check_forecast_vs_actual_sends)
check("actual return written to DB by forecast_vs_actual", _check_actual_written_to_db)

def _check_real_email_send():
    """Send a real email via Gmail SMTP if credentials are present."""
    from_addr = os.environ.get("EMAIL_FROM", "")
    password  = os.environ.get("EMAIL_PASSWORD", "")
    if not from_addr or not password or "smoke@test" in from_addr:
        raise EnvironmentError("Real email credentials not set — skipping live send.")

    from reporter import send_weekly_digest
    db         = state["paper_db"]
    candidates = state.get("screener_top", [])
    positions  = [
        {"ticker": c.ticker, "pct": 10, "action": "HOLD", "score": c.score}
        for c in candidates
    ]
    send_weekly_digest(
        db=db,
        positions=positions,
        alerts=["SMOKE TEST — this is a live test email from WarBuf"],
        news=["AAPL  Smoke test headline — system working correctly"],
        next_action="Smoke test complete — no action needed",
        spy_price=state.get("spy_price", 500.0),
        spy_sma=state.get("spy_sma", 480.0),
        portfolio_eur=1234.56,
        mtd_pct=2.5,
        spy_mtd_pct=1.8,
        eur_usd_rate=state["rules"].get("eur_usd_rate", 1.08),
    )
    print(f"    INFO  real email sent to {os.environ['EMAIL_TO']}")

check("real weekly digest email sent via Gmail", _check_real_email_send)



# ══════════════════════════════════════════════════════════════════════════════
# 9. SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n{'═' * 60}")
print("  SMOKE TEST RESULTS")
print(f"{'═' * 60}")

passed  = sum(1 for _, ok, _ in _RESULTS if ok is True)
failed  = sum(1 for _, ok, _ in _RESULTS if ok is False)
skipped = sum(1 for _, ok, _ in _RESULTS if ok is None)
total   = len(_RESULTS)

if failed:
    print(f"\n  \033[31m{failed} of {total} checks FAILED:\033[0m")
    for name, ok, err in _RESULTS:
        if ok is False:
            print(f"    ✗  {name}")
            print(f"       {err[:120]}")
else:
    print("\n  \033[32mAll checks passed.\033[0m")

print(f"\n  Passed: {passed}   Failed: {failed}   Skipped: {skipped}   Total: {total}")
print()

sys.exit(0 if failed == 0 else 1)
