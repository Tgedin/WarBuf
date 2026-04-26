"""
Scheduler entry point.

Jobs:
  weekly_job         — every Monday 09:00 Europe/Madrid
                       defensive: re-score positions, check sell triggers, send digest

  monthly_job        — first Monday of each month, 09:05
                       offensive: run full buy pipeline, LLM analysis, execute buys, send forecast

  nightly_backup_job — every day 03:00 UTC
                       dumps portfolio.db to a gzip backup in BACKUP_DIR (default: ./backups/)
                       keeps the last BACKUP_KEEP_DAYS (default: 30) files

  cache_prewarm_job  — every Sunday 22:00 Europe/Madrid
                       pre-fetches yfinance data for all watchlist + core ETF tickers
                       so Monday's job runs against a warm cache

See AGENTS.md for architecture, conventions, and the update rule.
"""
from __future__ import annotations

import gzip
import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from broker.ibkr import IBKRBroker
from broker.paper import PaperBroker
from core.agent import analyse_candidates, analyse_weekly
from core.fees import compute_fees
from core.market import get_last_price, get_news_headlines, get_spy_sma, is_risk_on
from core.scorer import FactorWeights
from core.screener import HardFilters, run_tier1_tier2
from db import Database, rules_hash
from reporter import send_weekly_digest

load_dotenv()

RULES_PATH  = "rules.yaml"
DB_PATH     = os.getenv("PORTFOLIO_DB_PATH", "portfolio.db")
BACKUP_DIR  = Path(os.getenv("BACKUP_DIR", "backups"))
BACKUP_KEEP_DAYS = int(os.getenv("BACKUP_KEEP_DAYS", "30"))


# ── Rule loading ──────────────────────────────────────────────────────────────

def _load_rules() -> dict:
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


def _make_broker(rules: dict, db: Database):
    if rules.get("paper_mode", True):
        return PaperBroker(db, eur_usd_rate=rules.get("eur_usd_rate", 1.0))
    return IBKRBroker()


def _make_filters(rules: dict) -> HardFilters:
    return HardFilters(
        min_market_cap_b=rules["min_market_cap_B"],
        max_pe_ratio=rules["max_pe_ratio"],
        min_revenue_growth_pct=rules["min_revenue_growth_pct"],
        max_debt_to_equity=rules["max_debt_to_equity"],
        sectors_excluded=rules.get("sectors_excluded", []),
    )


def _make_weights(rules: dict) -> FactorWeights:
    return FactorWeights(**rules["factor_weights"])


# ── Sell trigger detection (pure — no I/O) ────────────────────────────────────

def _detect_sell_trigger(
    ticker: str,
    qty: float,
    last_price_usd: float,
    return_pct: float | None,
    current_score: float | None,
    prev_score: float | None,
    stop_loss_pct: float,
    collapse_delta: float,
) -> tuple[str, float] | None:
    """Return (alert_text, notional_usd) if a sell should execute, else None.

    Stop-loss (full exit) takes priority over score collapse (50% trim).
    """
    notional = qty * last_price_usd
    if return_pct is not None and return_pct <= -stop_loss_pct:
        return (
            f"{ticker}  STOP-LOSS triggered ({return_pct:.1f}%) — sold all",
            notional,
        )
    if (
        current_score is not None
        and prev_score is not None
        and (prev_score - current_score) > collapse_delta
    ):
        return (
            f"{ticker}  SCORE COLLAPSE ({prev_score:.2f}→{current_score:.2f}) — trimmed 50%",
            notional / 2,
        )
    return None


# ── Weekly job ────────────────────────────────────────────────────────────────

def weekly_job() -> None:
    print(f"[WEEKLY] Starting — {date.today()}")
    rules  = _load_rules()
    db     = Database(DB_PATH)
    broker = _make_broker(rules, db)

    spy_price, spy_sma = get_spy_sma(rules["macro_guard"]["sma_days"])
    risk_on   = spy_price > spy_sma
    positions = broker.get_positions()

    # Re-score all held + watchlist tickers to catch weekly deterioration
    all_tickers = list({*positions.keys(), *rules.get("watchlist", [])})
    scored, _ = run_tier1_tier2(
        watchlist=all_tickers,
        filters=_make_filters(rules),
        weights=_make_weights(rules),
        momentum_lookback_days=rules.get("momentum_lookback_days", 365),
        momentum_skip_days=rules.get("momentum_skip_days", 30),
    )
    score_map = {s.ticker: s.score for s in scored}

    stop_loss_pct  = rules.get("stop_loss_pct", 15)
    collapse_delta = rules.get("score_collapse_delta", 0.25)
    eur_usd_rate   = rules.get("eur_usd_rate", 1.0)
    alerts: list[str] = []
    db_positions = db.get_positions()
    pos_display: list[dict] = []

    for ticker in positions:
        score  = score_map.get(ticker)
        db_pos = db_positions.get(ticker, {})
        cost_basis_eur = db_pos.get("avg_cost_basis_eur", 0.0)
        fees_eur       = db_pos.get("total_fees_eur", 0.0)
        qty            = db_pos.get("qty", 0.0)

        last_price_usd = get_last_price(ticker)
        return_pct: float | None = None
        value_eur = gross_gain_eur = net_gain_eur = None

        if last_price_usd is not None and cost_basis_eur > 0 and qty > 0:
            value_eur      = (qty * last_price_usd) / eur_usd_rate
            gross_gain_eur = value_eur - cost_basis_eur * qty
            net_gain_eur   = gross_gain_eur - fees_eur
            return_pct     = (gross_gain_eur / (cost_basis_eur * qty)) * 100

        if last_price_usd is not None and qty > 0:
            recent     = db.get_recent_decisions(ticker, limit=1)
            prev_score = recent[0].get("score") if recent else None
            trigger    = _detect_sell_trigger(
                ticker, qty, last_price_usd, return_pct, score, prev_score,
                stop_loss_pct, collapse_delta,
            )
            if trigger:
                alert_text, notional_usd = trigger
                broker.place_order(ticker, "sell", notional_usd)
                alerts.append(alert_text)

        entry: dict = {"ticker": ticker, "pct": 0, "action": "HOLD", "score": score}
        if value_eur is not None:
            entry.update({
                "value_eur":      value_eur,
                "gross_gain_eur": gross_gain_eur,
                "net_gain_eur":   net_gain_eur,
                "return_pct":     return_pct,
                "fees_eur":       fees_eur,
            })
        pos_display.append(entry)

    if not risk_on:
        alerts.append("MACRO GUARD ACTIVE — SPY below 200d SMA — no new buys")

    # Weekly LLM veto check for held positions
    held_scored = [s for s in scored if s.ticker in positions]
    if held_scored:
        held_news_map = {t: get_news_headlines(t, max_items=3) for t in positions}
        weekly_reports = analyse_weekly(
            candidates=held_scored,
            news=held_news_map,
            model=rules["llm_model"],
            max_tokens=rules.get("llm_max_tokens_weekly", 256),
        )
        for r in weekly_reports:
            if r.vetoed:
                alerts.append(f"{r.ticker}  LLM veto: {r.veto_reason}")

    # Collect material news for held positions
    news: list[str] = []
    for ticker in list(positions)[:5]:
        for headline in get_news_headlines(ticker, max_items=2):
            news.append(f"{ticker}  {headline}")

    is_first_mon = _is_first_monday()
    next_action = "Full analysis runs today." if is_first_mon else f"Full analysis: {_next_first_monday_str()}"

    send_weekly_digest(
        db=db,
        positions=pos_display,
        alerts=alerts,
        news=news,
        next_action=next_action,
        spy_price=spy_price,
        spy_sma=spy_sma,
        portfolio_eur=0.0,
        mtd_pct=0.0,
        spy_mtd_pct=0.0,
        eur_usd_rate=eur_usd_rate,
        dashboard_url=os.getenv("DASHBOARD_URL", ""),
    )

    db.close()
    print(f"[WEEKLY] Done.")


# ── Monthly job ───────────────────────────────────────────────────────────────

def monthly_job() -> None:
    if not _is_first_monday():
        return

    print(f"[MONTHLY] Starting full analysis — {date.today()}")
    rules  = _load_rules()
    db     = Database(DB_PATH)
    broker = _make_broker(rules, db)
    rhash  = rules_hash(RULES_PATH)

    if not is_risk_on(rules["macro_guard"]["sma_days"]):
        print("[MONTHLY] Macro guard active — skipping buy analysis")
        db.close()
        return

    top5, rejected = run_tier1_tier2(
        watchlist=rules.get("watchlist", []),
        filters=_make_filters(rules),
        weights=_make_weights(rules),
        momentum_lookback_days=rules.get("momentum_lookback_days", 365),
        momentum_skip_days=rules.get("momentum_skip_days", 30),
    )
    print(f"[MONTHLY] Screened: {len(top5)} passed, {len(rejected)} rejected")

    if not top5:
        db.close()
        return

    news = {c.ticker: get_news_headlines(c.ticker) for c in top5}

    # Inject last 3 decisions per ticker so LLM can audit its own prior reasoning
    prior_decisions = {c.ticker: db.get_recent_decisions(c.ticker, limit=3) for c in top5}

    reports = analyse_candidates(
        candidates=top5,
        news=news,
        model=rules.get("llm_model_monthly", rules["llm_model"]),
        max_tokens=rules.get("llm_max_tokens_monthly", 2500),
        prior_decisions=prior_decisions,
        fallback_models=rules.get("llm_model_monthly_fallbacks", []),
        rules_context=rules,
    )
    report_map = {r.ticker: r for r in reports}
    vetoed = {r.ticker for r in reports if r.vetoed}

    min_notional_usd = rules.get("min_position_eur", 300) * rules.get("eur_usd_rate", 1.08)

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
        )

        if candidate.ticker in vetoed:
            print(f"[MONTHLY] {candidate.ticker} vetoed: {report.veto_reason if report else 'unknown'}")
            continue

        eur_usd_rate = rules.get("eur_usd_rate", 1.0)
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

        except Exception as exc:
            print(f"[MONTHLY] Order failed for {candidate.ticker}: {exc}")

    db.close()
    print("[MONTHLY] Done.")


# ── Utilities ─────────────────────────────────────────────────────────────────

def _is_first_monday() -> bool:
    today = date.today()
    return today.weekday() == 0 and today.day <= 7


def _next_first_monday_str() -> str:
    d = date.today().replace(day=1)
    next_month = (
        d.replace(month=d.month % 12 + 1)
        if d.month < 12
        else d.replace(year=d.year + 1, month=1)
    )
    while next_month.weekday() != 0:
        next_month += timedelta(days=1)
    return next_month.strftime("%b %d")


# ── Idle-time jobs ────────────────────────────────────────────────────────────

def nightly_backup_job() -> None:
    """Compress portfolio.db and rotate old backups."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    dest = BACKUP_DIR / f"portfolio_{stamp}.db.gz"
    with open(DB_PATH, "rb") as src, gzip.open(dest, "wb") as gz:
        shutil.copyfileobj(src, gz)
    print(f"[BACKUP] Written: {dest}")

    cutoff = date.today() - timedelta(days=BACKUP_KEEP_DAYS)
    for old in BACKUP_DIR.glob("portfolio_*.db.gz"):
        try:
            file_date = date.fromisoformat(old.stem.removeprefix("portfolio_").removesuffix(".db"))
            if file_date < cutoff:
                old.unlink()
                print(f"[BACKUP] Pruned: {old.name}")
        except ValueError:
            pass  # skip files that don't match the naming pattern


def cache_prewarm_job() -> None:
    """Pre-fetch yfinance data for all watchlist tickers before Monday's job."""
    rules = _load_rules()
    tickers = list(rules.get("watchlist", [])) + list(rules.get("core_etfs", {}).keys())
    print(f"[PREWARM] Warming cache for {len(tickers)} tickers …")
    for ticker in tickers:
        try:
            get_last_price(ticker)
        except Exception as exc:
            print(f"[PREWARM] {ticker} skipped: {exc}")
    print("[PREWARM] Done.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="Europe/Madrid")
    scheduler.add_job(weekly_job,         "cron", day_of_week="mon", hour=9,  minute=0)
    scheduler.add_job(monthly_job,        "cron", day_of_week="mon", hour=9,  minute=5)
    scheduler.add_job(cache_prewarm_job,  "cron", day_of_week="sun", hour=22, minute=0)
    scheduler.add_job(
        nightly_backup_job, "cron", hour=3, minute=0,
        timezone="UTC",
    )
    print("WarBuf scheduler started. Waiting for Monday 09:00 Europe/Madrid.")
    scheduler.start()
