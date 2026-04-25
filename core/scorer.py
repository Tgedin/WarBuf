"""
4-factor composite scorer.

All functions are pure: given the same inputs, always return the same output.
No I/O, no side effects. Safe to test without mocking anything.

Factors (academic sources):
  Quality       — Novy-Marx 2013, AQR
  Value         — Fama-French 1992
  Momentum      — Jegadeesh-Titman 1993 (12-1 month)
  Profitability — Novy-Marx 2013
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    roe: float | None                    # return on equity
    fcf_margin: float | None             # free cash flow / revenue
    debt_to_equity: float | None         # % (e.g. 50 = 50%)
    earnings_yield: float | None         # E/P = 1 / P/E
    gross_profit_to_assets: float | None # Novy-Marx profitability
    momentum_12_1: float | None          # 12m return skipping last 30 days
    market_cap_b: float | None           # USD billions
    revenue_growth: float | None         # YoY fraction (e.g. 0.10 = 10%)
    sector: str | None


@dataclass(frozen=True)
class FactorWeights:
    quality: float
    value: float
    momentum: float
    profitability: float

    def __post_init__(self) -> None:
        total = self.quality + self.value + self.momentum + self.profitability
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"Factor weights must sum to 1.0, got {total:.6f}")


@dataclass(frozen=True)
class ScoredTicker:
    ticker: str
    score: float           # composite 0–1
    quality_rank: float
    value_rank: float
    momentum_rank: float
    profitability_rank: float
    fundamentals: "Fundamentals | None" = None   # raw metrics; set by score_watchlist


# At this D/E %, the quality penalty is 100% (worst possible debt load).
# Based on Fama-French leverage benchmarks; above this D/E adds no new signal.
DE_SATURATION_PCT = 200.0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _quality_signal(f: Fundamentals) -> float | None:
    """
    Quality = ROE × FCF_margin × (1 − D/E normalised).
    Returns None if ROE or FCF margin is missing.
    D/E is optional — defaults to 0 (no debt penalty).
    """
    if f.roe is None or f.fcf_margin is None:
        return None
    de_norm = min((f.debt_to_equity or 0.0) / DE_SATURATION_PCT, 1.0)
    return f.roe * max(f.fcf_margin, 0.0) * (1.0 - de_norm)


def _cross_rank(raw: list[float | None]) -> list[float]:
    """
    Cross-sectional rank mapped to [0, 1].
    Higher raw value → higher rank.
    None / NaN values receive rank 0 (treated as worst).
    Single valid value gets 0.5 (neutral, not penalised).
    """
    n = len(raw)
    if n == 0:
        return []

    valid = [
        (v, i)
        for i, v in enumerate(raw)
        if v is not None and math.isfinite(v)
    ]
    ranks = [0.0] * n

    if not valid:
        return ranks

    valid.sort(key=lambda x: x[0])
    n_valid = len(valid)

    for position, (_, original_idx) in enumerate(valid):
        ranks[original_idx] = position / (n_valid - 1) if n_valid > 1 else 0.5

    return ranks


# ── Public API ────────────────────────────────────────────────────────────────

def score_watchlist(
    fundamentals: Sequence[Fundamentals],
    weights: FactorWeights,
) -> list[ScoredTicker]:
    """
    Score and rank a list of tickers using the 4-factor composite.

    Each factor is ranked cross-sectionally (0–1) before weighting.
    Returns list sorted by composite score descending (best first).
    """
    if not fundamentals:
        return []

    quality_signals       = [_quality_signal(f) for f in fundamentals]
    value_signals         = [f.earnings_yield for f in fundamentals]
    momentum_signals      = [f.momentum_12_1 for f in fundamentals]
    profitability_signals = [f.gross_profit_to_assets for f in fundamentals]

    q_ranks = _cross_rank(quality_signals)
    v_ranks = _cross_rank(value_signals)
    m_ranks = _cross_rank(momentum_signals)
    p_ranks = _cross_rank(profitability_signals)

    scored: list[ScoredTicker] = []
    for i, f in enumerate(fundamentals):
        composite = (
            weights.quality         * q_ranks[i]
            + weights.value         * v_ranks[i]
            + weights.momentum      * m_ranks[i]
            + weights.profitability * p_ranks[i]
        )
        scored.append(ScoredTicker(
            ticker=f.ticker,
            score=round(composite, 4),
            quality_rank=round(q_ranks[i], 4),
            value_rank=round(v_ranks[i], 4),
            momentum_rank=round(m_ranks[i], 4),
            profitability_rank=round(p_ranks[i], 4),
            fundamentals=f,
        ))

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
