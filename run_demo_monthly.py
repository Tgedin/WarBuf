#!/usr/bin/env python3
"""
run_demo_monthly.py — Trigger a real monthly analysis run against demo.db.

Calls the full monthly pipeline (Tier 1 + Tier 2 + Tier 3 LLM) using the
top 5 watchlist tickers that pass filters, writes decisions + a forecast to
demo.db, then prints the Streamlit launch command.

Usage:
    python run_demo_monthly.py
"""
from __future__ import annotations

import os

import yaml
from dotenv import load_dotenv

load_dotenv()

DB_PATH    = "demo.db"
RULES_PATH = "rules.yaml"

os.environ["PORTFOLIO_DB_PATH"] = DB_PATH

from core.agent import analyse_candidates  # noqa: E402
from core.fees import compute_fees  # noqa: E402
from core.market import get_news_headlines, is_risk_on  # noqa: E402
from core.scorer import FactorWeights  # noqa: E402
from core.screener import HardFilters, run_tier1_tier2  # noqa: E402
from broker.paper import PaperBroker  # noqa: E402
from db import Database, rules_hash  # noqa: E402
from reporter import send_monthly_forecast  # noqa: E402


def main() -> None:
    print("=== WarBuf demo monthly run ===")
    print(f"DB: {DB_PATH}  |  rules: {RULES_PATH}\n")

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)

    db     = Database(DB_PATH)
    broker = PaperBroker(db, eur_usd_rate=rules.get("eur_usd_rate", 1.08))
    rhash  = rules_hash(RULES_PATH)

    filters = HardFilters(
        min_market_cap_b=rules["min_market_cap_B"],
        max_pe_ratio=rules["max_pe_ratio"],
        min_revenue_growth_pct=rules["min_revenue_growth_pct"],
        max_debt_to_equity=rules["max_debt_to_equity"],
        sectors_excluded=rules.get("sectors_excluded", []),
    )
    weights = FactorWeights(**rules["factor_weights"])

    # Macro guard — warn but don't block (demo mode)
    risk = is_risk_on(rules["macro_guard"]["sma_days"])
    if not risk:
        print("[DEMO] Macro guard would block buys today (SPY < 200d SMA) — running anyway for demo.\n")

    print("[1/4] Screening watchlist …")
    top5, rejected = run_tier1_tier2(
        watchlist=rules.get("watchlist", []),
        filters=filters,
        weights=weights,
        momentum_lookback_days=rules.get("momentum_lookback_days", 365),
        momentum_skip_days=rules.get("momentum_skip_days", 30),
    )
    print(f"      {len(top5)} passed Tier 1+2, {len(rejected)} rejected")
    if not top5:
        print("No candidates passed filters. Exiting.")
        db.close()
        return

    for c in top5:
        print(f"      {c.ticker:6s}  score={c.score:.3f}")

    print("\n[2/4] Fetching news …")
    news = {c.ticker: get_news_headlines(c.ticker) for c in top5}

    print("\n[3/4] Running LLM analysis (o3-mini) — this may take 30–60s …")
    prior_decisions = {c.ticker: db.get_recent_decisions(c.ticker, limit=3) for c in top5}

    reports = analyse_candidates(
        candidates=top5,
        news=news,
        model=rules.get("llm_model_monthly", rules["llm_model"]),
        max_tokens=rules.get("llm_max_tokens_monthly", 4000),
        prior_decisions=prior_decisions,
        fallback_models=rules.get("llm_model_monthly_fallbacks", []),
        rules_context=rules,
    )
    report_map = {r.ticker: r for r in reports}
    vetoed = {r.ticker for r in reports if r.vetoed}

    print("\n[4/4] Recording decisions + placing paper orders …")
    min_notional_usd = rules.get("min_position_eur", 300) * rules.get("eur_usd_rate", 1.08)
    eur_usd_rate = rules.get("eur_usd_rate", 1.08)

    bought: list[dict] = []
    for candidate in top5:
        report = report_map.get(candidate.ticker)
        action = "VETO" if candidate.ticker in vetoed else "BUY"

        db.record_decision(
            ticker=candidate.ticker,
            action=action,
            score=candidate.score,
            rules_hash=rhash,
            vetoed=bool(report and report.vetoed),
            veto_reason=report.veto_reason if report else None,
            bull_case=report.bull_case if report else None,
            bear_case=report.bear_case if report else None,
            data_gaps=report.data_gaps if report else None,
            model_confidence=report.model_confidence if report else None,
            confidence_reason=report.confidence_reason if report else None,
            self_critique=report.self_critique if report else None,
            algorithm_feedback=report.algorithm_feedback if report else None,
            data_request=report.data_request if report else None,
        )

        status = "VETOED" if candidate.ticker in vetoed else "BUY"
        print(f"      {candidate.ticker:6s}  {status:6s}  confidence={getattr(report, 'model_confidence', '?')}")
        if report and report.vetoed:
            print(f"             veto: {report.veto_reason}")
            continue

        try:
            result = broker.place_order(candidate.ticker, "buy", min_notional_usd)
            fees = compute_fees("buy", result.qty, min_notional_usd)
            db.record_trade(
                ticker=result.ticker,
                side="buy",
                qty=result.qty,
                price_usd=result.filled_price_usd,
                fees_usd=fees.total_usd,
                net_cost_basis=min_notional_usd + fees.total_usd,
                ibkr_order_id=result.order_id,
                eur_usd_rate=eur_usd_rate,
            )
            bought.append({"ticker": result.ticker, "qty": result.qty, "price": result.filled_price_usd})
        except Exception as exc:
            print(f"      {candidate.ticker} order failed: {exc}")

    # Save forecast
    forecast_lines = []
    for c in top5:
        r = report_map.get(c.ticker)
        conf = getattr(r, "model_confidence", "low")
        bull = getattr(r, "bull_case", "")
        forecast_lines.append(f"{c.ticker} (score={c.score:.2f}, {conf}): {bull}")

    try:
        send_monthly_forecast(
            month_label=__import__("datetime").date.today().strftime("%B %Y"),
            macro_regime="Risk-on" if risk else "Risk-off (SPY < 200d SMA)",
            expected_low=0.03,
            expected_high=0.08,
            downside=-0.05,
            key_risk="Macro deterioration or Fed pivot reversal",
            position_outlooks=[
                {"ticker": c.ticker,
                 "outlook": "BUY" if c.ticker not in vetoed else "VETO",
                 "note": (report_map[c.ticker].bull_case[:60] if c.ticker in report_map else "")[:60]}
                for c in top5
            ],
            planned_action=f"Buy {len(bought)} satellite positions via paper broker.",
            db=db,
            dashboard_url=os.getenv("DASHBOARD_URL", "http://localhost:8501"),
        )
        print("\n      Forecast saved to DB.")
    except Exception as exc:
        print(f"\n      Forecast save skipped: {exc}")

    db.close()

    print("\n=== Done ===")
    print(f"Wrote {len(top5)} decisions + {len(bought)} paper trades to {DB_PATH}")
    print("\nLaunch dashboard:")
    print(f"  PORTFOLIO_DB_PATH={DB_PATH} .venv/bin/streamlit run dashboard.py")


if __name__ == "__main__":
    main()
