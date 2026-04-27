"""
Market data access via yfinance with a simple file-based cache.

Cache layout: .cache/<key>.json
TTL: 24h for fundamentals, 1h for prices/momentum.

All public functions raise RuntimeError on unrecoverable failures
(missing data is returned as None in the Fundamentals object).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import yfinance as yf

from core.scorer import Fundamentals

CACHE_DIR = Path(".cache")
FUNDAMENTALS_TTL_S = 86_400   # 24 hours
PRICE_TTL_S = 3_600           # 1 hour


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _load_cache(path: Path, ttl: int) -> dict | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data))
    except OSError as exc:
        print(f"[CACHE] Write failed for {path}: {exc} — continuing without cache")


def _safe_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_fundamentals(ticker: str) -> Fundamentals:
    """Fetch fundamentals for one ticker, with 24h file cache."""
    path = _cache_path(f"{ticker}_fundamentals")
    cached = _load_cache(path, FUNDAMENTALS_TTL_S)

    if cached is None:
        info = yf.Ticker(ticker).info
        cached = {
            "roe":                    info.get("returnOnEquity"),
            "fcf_margin":             _compute_fcf_margin(info),
            "debt_to_equity":         info.get("debtToEquity"),
            "earnings_yield":         _compute_earnings_yield(info),
            "gross_profit_to_assets": _compute_gp_to_assets(info),
            "market_cap_b":           _compute_market_cap_b(info),
            "revenue_growth":         info.get("revenueGrowth"),
            "sector":                 info.get("sector"),
        }
        _save_cache(path, cached)

    return Fundamentals(
        ticker=ticker,
        roe=_safe_float(cached.get("roe")),
        fcf_margin=_safe_float(cached.get("fcf_margin")),
        debt_to_equity=_safe_float(cached.get("debt_to_equity")),
        earnings_yield=_safe_float(cached.get("earnings_yield")),
        gross_profit_to_assets=_safe_float(cached.get("gross_profit_to_assets")),
        momentum_12_1=None,   # fetched separately — requires price history
        market_cap_b=_safe_float(cached.get("market_cap_b")),
        revenue_growth=_safe_float(cached.get("revenue_growth")),
        sector=cached.get("sector"),
    )


def get_momentum(ticker: str, lookback_days: int = 365, skip_days: int = 30) -> float | None:
    """
    Jegadeesh-Titman 12-1 momentum: return over lookback_days, skipping the
    most recent skip_days. Returns fractional return or None if unavailable.
    """
    path = _cache_path(f"{ticker}_mom_{lookback_days}_{skip_days}")
    cached = _load_cache(path, PRICE_TTL_S)

    if cached is None:
        try:
            hist = yf.Ticker(ticker).history(period=f"{lookback_days + skip_days + 15}d")
            if hist.empty or len(hist) < skip_days + 2:
                return None
            closes = hist["Close"]
            recent_price = float(closes.iloc[-(skip_days + 1)])
            old_price = float(closes.iloc[0])
            if old_price == 0:
                return None
            momentum = (recent_price - old_price) / old_price
            cached = {"momentum": momentum}
        except Exception as exc:
            print(f"[MARKET] Momentum fetch failed for {ticker}: {exc}")
            return None
        _save_cache(path, cached)

    return _safe_float(cached.get("momentum"))


def get_spy_sma(sma_days: int = 200) -> tuple[float, float]:
    """
    Returns (current_price, sma_value) for SPY.
    Raises RuntimeError if data cannot be fetched.
    """
    path = _cache_path(f"SPY_sma_{sma_days}")
    cached = _load_cache(path, PRICE_TTL_S)

    if cached is None:
        try:
            hist = yf.Ticker("SPY").history(period=f"{sma_days + 30}d")
            if len(hist) < sma_days:
                raise RuntimeError(f"Insufficient SPY history for {sma_days}-day SMA")
            closes = hist["Close"]
            current = float(closes.iloc[-1])
            sma = float(closes.tail(sma_days).mean())
            cached = {"current": current, "sma": sma}
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch SPY data: {exc}") from exc
        _save_cache(path, cached)

    return float(cached["current"]), float(cached["sma"])


def is_risk_on(sma_days: int = 200) -> bool:
    """True if SPY is above its SMA (macro guard is clear — OK to buy)."""
    current, sma = get_spy_sma(sma_days)
    return current > sma


def get_news_headlines(ticker: str, max_items: int = 5) -> list[str]:
    """Return up to max_items recent news headlines. Empty list on failure."""
    try:
        news = yf.Ticker(ticker).news or []
        return [
            item.get("title", "")
            for item in news[:max_items]
            if item.get("title")
        ]
    except Exception:
        return []


def get_last_price(ticker: str) -> float | None:
    """
    Return the most recent closing price for a ticker in USD (1h cache).
    Returns None if data cannot be fetched.
    """
    path = _cache_path(f"{ticker}_price")
    cached = _load_cache(path, PRICE_TTL_S)

    if cached is None:
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            cached = {"price": price}
        except Exception as exc:
            print(f"[MARKET] Price fetch failed for {ticker}: {exc}")
            return None
        _save_cache(path, cached)

    return _safe_float(cached.get("price"))


def get_open_price(ticker: str) -> float | None:
    """
    Return today's opening price for a ticker in USD (1h cache).

    Uses the first 1-minute candle of the current trading day, which closely
    matches what an IBKR MKT DAY order would fill at on the open.
    Falls back to the last close price if the market has not yet opened.
    Returns None on any unrecoverable failure.
    """
    path = _cache_path(f"{ticker}_open")
    cached = _load_cache(path, PRICE_TTL_S)

    if cached is None:
        try:
            hist = yf.Ticker(ticker).history(period="1d", interval="1m")
            if hist.empty:
                # Market not yet open — fall back to last close
                return get_last_price(ticker)
            price = float(hist["Open"].iloc[0])
            cached = {"price": price}
        except Exception as exc:
            print(f"[MARKET] Open price fetch failed for {ticker}: {exc}")
            return get_last_price(ticker)
        _save_cache(path, cached)

    return _safe_float(cached.get("price"))


# ── Private helpers ───────────────────────────────────────────────────────────

def _compute_fcf_margin(info: dict) -> float | None:
    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    if fcf is None or rev is None or rev == 0:
        return None
    try:
        return float(fcf) / float(rev)
    except (TypeError, ZeroDivisionError):
        return None


def _compute_earnings_yield(info: dict) -> float | None:
    pe = info.get("trailingPE")
    if pe is None or pe <= 0:
        return None
    try:
        return 1.0 / float(pe)
    except (TypeError, ZeroDivisionError):
        return None


def _compute_gp_to_assets(info: dict) -> float | None:
    gp = info.get("grossProfits")
    assets = info.get("totalAssets")
    if gp is None or assets is None or assets == 0:
        return None
    try:
        return float(gp) / float(assets)
    except (TypeError, ZeroDivisionError):
        return None


def _compute_market_cap_b(info: dict) -> float | None:
    mc = info.get("marketCap")
    if mc is None:
        return None
    try:
        return float(mc) / 1e9
    except (TypeError, ValueError):
        return None
