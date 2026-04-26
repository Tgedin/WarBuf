"""
LLM analysis layer (Tier 3 of the screening pipeline).

Two modes:
  weekly  — veto-only, ~256 tokens, fast. Answers: sell this week? yes/no + reason.
  monthly — full agentic analysis, two-turn, historical memory injected from DB.
            ~2500 tokens. Self-critical. The LLM can audit its own prior reasoning.

AnalysisReport fields:
  vetoed / veto_reason   — binary sell signal (concrete evidence only)
  bull_case / bear_case  — thesis + primary risk
  data_gaps              — what the model wishes it had (pipeline improvement signal)
  data_request           — explicit request for more data next run
  self_critique          — where the model's own reasoning may be anchored or wrong
  model_confidence       — high / medium / low
  confidence_reason      — one-sentence justification

Fail-open: LLM failure → neutral non-vetoing reports. Math score governs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from core.llm_provider import call_llm, call_llm_with_fallbacks
from core.scorer import ScoredTicker

# ── Prompts ───────────────────────────────────────────────────────────────────

_WEEKLY_PROMPT = """\
You are a risk-monitor for a long-term buy-and-hold portfolio.
Your ONLY job this week is to flag concrete sell signals.

VETO (recommend immediate sell) if — and ONLY if — there is specific, \
recent, publicly available evidence of:
  - Active fraud, accounting scandal, or SEC/EU enforcement action
  - A regulatory ruling that permanently impairs the core business model
  - Revenue decline >20% YoY in the most recent reported quarter

Default is NO VETO. Uncertainty, valuation concerns, or macro sentiment are NOT veto reasons.

Held positions with current scores:
{candidates_json}

Recent news:
{news_json}

Respond with a JSON array ONLY — no prose:
[{{"ticker": "X", "vetoed": false, "veto_reason": null}}]
"""

_MONTHLY_EXPLORE_PROMPT = """\
You are a rigorous, self-critical long-term value investor.
You will analyse these stock candidates for a buy-and-hold portfolio (2–5 year horizon).

The quantitative screening has already run two tiers:
  Tier 1: Hard filters (market cap, P/E, D/E, revenue growth, sector)
  Tier 2: 4-factor composite = 0.35×quality + 0.25×value + 0.25×momentum + 0.15×profitability
          All factors are cross-sectionally ranked [0,1] before combining.

Your role is Tier 3: independent judgement, pattern recognition, and self-critique.
You are also expected to give actionable feedback to improve the algorithm itself.

ACTIVE STRATEGY RULES (the exact parameters governing this run):
{rules_block}

PRIOR ANALYSES (your own reasoning from past months — audit yourself):
{memory_block}

CANDIDATES (factor ranks [0–1] and raw metrics included for deeper analysis):
{candidates_json}

RECENT NEWS:
{news_json}

Step 1 — Before writing your analysis, flag:
  a) Any data you are missing that would materially change your view
  b) Any candidates where the factor ranks may be misleading (e.g. momentum from a one-off
     event, quality inflated by a buyback, value trap where low P/E = structural decline)
  c) Any contradictions between the news and the quantitative score
  d) Any prior analyses where your own reasoning turned out to be wrong
  e) Any strategy parameters above that seem poorly calibrated for current market conditions
     (e.g. a PE cap that is too tight, a weight that over- or under-emphasises a factor)

Reply with your Step 1 observations as plain text. Be specific and critical.
"""

_MONTHLY_CONCLUDE_PROMPT = """\
Step 1 observations:
{exploration}

You MUST output exactly one JSON object for EACH of these tickers (no omissions): {ticker_list}

Now write your full analysis as a JSON array ONLY — no prose before or after:
[
  {{
    "ticker": "AAPL",
    "vetoed": false,
    "veto_reason": null,
    "bull_case": "1–2 sentences: why the thesis holds given both the score and the news",
    "bear_case": "1–2 sentences: most concrete near-term risk with specific evidence",
    "data_gaps": ["specific data point you wish you had and why it matters"],
    "data_request": "one specific actionable thing to fetch next month, or empty string",
    "model_confidence": "high|medium|low",
    "confidence_reason": "one sentence explaining the confidence level",
    "self_critique": "where your reasoning may be anchored, biased, or wrong",
    "algorithm_feedback": "which factor(s) are least reliable for this stock and why; what weight or data change would most improve signal quality for this type of company; cite the specific metric values that support your view"
  }}
]

Rules:
- VETO only for fraud / enforcement / revenue -20% YoY. Uncertainty is NOT a veto.
- Be constructively critical of your own prior analyses if they were wrong.
- data_request must be actionable and specific.
- algorithm_feedback must name the factor, name the raw metric, and propose the fix.
"""

# ── Required fields for monthly response validation ───────────────────────────

_REQUIRED_FIELDS: dict[str, type] = {
    "ticker":             str,
    "vetoed":             bool,
    "bull_case":          str,
    "bear_case":          str,
    "data_gaps":          list,
    "model_confidence":   str,
    "confidence_reason":  str,
    "self_critique":      str,
    "algorithm_feedback": str,
}
_VALID_CONFIDENCE = {"high", "medium", "low"}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AnalysisReport:
    ticker: str
    vetoed: bool
    veto_reason: str | None
    bull_case: str
    bear_case: str
    data_gaps: list[str]
    data_request: str          # explicit request for next month's data fetch
    model_confidence: str      # "high" | "medium" | "low"
    confidence_reason: str
    self_critique: str
    algorithm_feedback: str    # factor reliability + suggested weight/data improvements

    def to_dict(self) -> dict:
        return {
            "ticker":             self.ticker,
            "vetoed":             self.vetoed,
            "veto_reason":        self.veto_reason,
            "bull_case":          self.bull_case,
            "bear_case":          self.bear_case,
            "data_gaps":          self.data_gaps,
            "data_request":       self.data_request,
            "model_confidence":   self.model_confidence,
            "confidence_reason":  self.confidence_reason,
            "self_critique":      self.self_critique,
            "algorithm_feedback": self.algorithm_feedback,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def analyse_weekly(
    candidates: list[ScoredTicker],
    news: dict[str, list[str]],
    model: str,
    max_tokens: int = 256,
) -> list[AnalysisReport]:
    """
    Weekly veto-only pass. Fast, minimal tokens.

    Returns one AnalysisReport per candidate (only vetoed/veto_reason are meaningful).
    Fail-open: LLM failure → all non-vetoed.
    """
    if not candidates:
        return []

    candidates_json = json.dumps(
        [{"ticker": c.ticker, "score": c.score} for c in candidates], indent=2
    )
    news_json = json.dumps(news, indent=2)
    prompt = _WEEKLY_PROMPT.format(
        candidates_json=candidates_json,
        news_json=news_json,
    )

    try:
        raw    = call_llm(prompt, model, max_tokens)
        parsed = _extract_json_array(raw)
        return [_parse_weekly_report(item, candidates) for item in parsed]
    except Exception as exc:
        print(f"[AGENT] Weekly LLM check failed — no vetoes applied: {exc}")
        return [_neutral_report(c.ticker) for c in candidates]


def analyse_candidates(
    candidates: list[ScoredTicker],
    news: dict[str, list[str]],
    model: str,
    max_tokens: int = 2500,
    prior_decisions: dict[str, list[dict]] | None = None,
    fallback_models: list[str] | None = None,
    rules_context: dict | None = None,
) -> list[AnalysisReport]:
    """
    Monthly full agentic analysis with two-turn reasoning and historical memory.

    prior_decisions: {ticker: [last N decision dicts from DB]} — injected as
    memory so the LLM can audit its own past reasoning.

    fallback_models: if provided, primary model is tried first, then each fallback
    in order. Fail-open: all failures → neutral reports (math score governs).

    The candidates_json block includes factor-level ranks and raw metric values
    so the LLM has enough context to give actionable algorithm improvement feedback.
    """
    if not candidates:
        return []

    candidates_json = json.dumps(
        [_candidate_context(c) for c in candidates], indent=2
    )
    news_json    = json.dumps(news, indent=2)
    memory_block = _build_memory_block(prior_decisions or {}, candidates)
    rules_block  = _build_rules_block(rules_context or {})

    all_models = [model] + (fallback_models or [])

    def _call(prompt: str, tokens: int) -> str:
        last_exc: Exception | None = None
        for m in all_models:
            try:
                return call_llm(prompt, m, tokens)
            except RuntimeError as exc:
                print(f"[LLM] {m} failed, trying next fallback: {exc}")
                last_exc = exc
        raise RuntimeError(f"All models failed. Last error: {last_exc}") from last_exc

    # Turn 1 — explore
    explore_prompt = _MONTHLY_EXPLORE_PROMPT.format(
        rules_block=rules_block,
        memory_block=memory_block,
        candidates_json=candidates_json,
        news_json=news_json,
    )
    try:
        exploration = _call(explore_prompt, min(max_tokens // 3, 800))
    except Exception as exc:
        print(f"[AGENT] Monthly explore turn failed — skipping to conclude: {exc}")
        exploration = "Exploration step failed — proceeding with available data."

    # Turn 2 — conclude
    ticker_list = ", ".join(c.ticker for c in candidates)
    conclude_prompt = _MONTHLY_CONCLUDE_PROMPT.format(
        exploration=exploration, ticker_list=ticker_list
    )
    try:
        raw     = _call(conclude_prompt, max_tokens)
        parsed  = _extract_json_array(raw)
        reports = [_parse_report(item, candidates) for item in parsed]
        _validate_all_tickers_covered(reports, candidates)
        return reports
    except Exception as exc:
        print(f"[AGENT] Monthly LLM analysis failed — returning neutral reports: {exc}")
        return [_neutral_report(c.ticker) for c in candidates]


def _build_rules_block(rules: dict) -> str:
    """Format the active rules.yaml parameters as a readable string for the LLM prompt."""
    if not rules:
        return "Rules not provided."

    weights = rules.get("factor_weights", {})
    lines = [
        "Factor weights:",
        f"  quality={weights.get('quality', '?')}  value={weights.get('value', '?')}  "
        f"momentum={weights.get('momentum', '?')}  profitability={weights.get('profitability', '?')}",
        "Hard filters (Tier 1):",
        f"  min_market_cap={rules.get('min_market_cap_B', '?')}B USD"
        f"  max_PE={rules.get('max_pe_ratio', '?')}"
        f"  min_revenue_growth={rules.get('min_revenue_growth_pct', '?')}%"
        f"  max_D/E={rules.get('max_debt_to_equity', '?')}%",
        f"  excluded_sectors={rules.get('sectors_excluded', [])}",
        "Portfolio construction:",
        f"  max_positions={rules.get('max_positions', '?')} satellite"
        f"  max_position_pct={rules.get('max_position_pct', '?')}%"
        f"  min_position_eur=€{rules.get('min_position_eur', '?')}"
        f"  cash_floor={rules.get('cash_floor_pct', '?')}%",
        "Risk:",
        f"  stop_loss={rules.get('stop_loss_pct', '?')}%"
        f"  score_collapse_delta={rules.get('score_collapse_delta', '?')}",
        f"  min_hold_months={rules.get('min_hold_months', '?')}",
        "Macro guard:",
        f"  SPY must be above its {rules.get('macro_guard', {}).get('sma_days', '?')}-day SMA for new buys",
    ]
    return "\n".join(lines)


# ── Memory injection ──────────────────────────────────────────────────────────

def _build_memory_block(
    prior_decisions: dict[str, list[dict]],
    candidates: list[ScoredTicker],
) -> str:
    """Format prior DB decisions as a readable memory string for the LLM prompt."""
    if not prior_decisions:
        return "No prior analyses on record."

    lines: list[str] = []
    for candidate in candidates:
        history = prior_decisions.get(candidate.ticker, [])
        if not history:
            lines.append(f"{candidate.ticker}: no prior analysis.")
            continue
        lines.append(f"{candidate.ticker}:")
        for d in history:
            lines.append(
                f"  {d.get('date', '?')}: "
                f"action={d.get('action', '?')} score={d.get('score', '?'):.2f} "
                f"bull={d.get('bull_case', '')[:80]} | "
                f"bear={d.get('bear_case', '')[:80]} | "
                f"critique={d.get('self_critique', '')[:80]}"
            )
    return "\n".join(lines)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_json_array(raw: str) -> list[dict]:
    """Extract the outermost JSON array, tolerating surrounding prose."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON array in LLM response: {raw[:400]!r}")
    return json.loads(match.group())


def _parse_weekly_report(item: dict, candidates: list[ScoredTicker]) -> AnalysisReport:
    """Parse a veto-only weekly response item."""
    if not isinstance(item, dict):
        raise ValueError(f"Expected dict in weekly LLM response, got {type(item).__name__}")
    ticker = item.get("ticker")
    if not isinstance(ticker, str):
        raise ValueError(f"'ticker' must be a string, got {ticker!r}")
    known = {c.ticker for c in candidates}
    if ticker not in known:
        raise ValueError(f"Weekly LLM returned unknown ticker {ticker!r}")
    return AnalysisReport(
        ticker=ticker,
        vetoed=bool(item.get("vetoed", False)),
        veto_reason=item.get("veto_reason") or None,
        bull_case="",
        bear_case="",
        data_gaps=[],
        data_request="",
        model_confidence="low",
        confidence_reason="Weekly veto-only pass.",
        self_critique="",
        algorithm_feedback="",
    )


def _parse_report(item: dict, candidates: list[ScoredTicker]) -> AnalysisReport:
    """Parse and validate a single monthly LLM response item."""
    if not isinstance(item, dict):
        raise ValueError(f"Expected dict in LLM response, got {type(item).__name__}")

    ticker = item.get("ticker")
    if not isinstance(ticker, str):
        raise ValueError(f"'ticker' must be a string, got {ticker!r}")

    known_tickers = {c.ticker for c in candidates}
    if ticker not in known_tickers:
        raise ValueError(f"LLM returned unknown ticker {ticker!r}. Expected one of {known_tickers}")

    for key, expected_type in _REQUIRED_FIELDS.items():
        val = item.get(key)
        if val is None and key != "veto_reason":
            raise ValueError(f"Missing required field '{key}' for {ticker}")
        if val is not None and not isinstance(val, expected_type):
            raise ValueError(
                f"Field '{key}' for {ticker} must be {expected_type.__name__}, "
                f"got {type(val).__name__}"
            )

    confidence = item.get("model_confidence", "").lower()
    if confidence not in _VALID_CONFIDENCE:
        raise ValueError(
            f"model_confidence for {ticker} must be one of {_VALID_CONFIDENCE}, "
            f"got {confidence!r}"
        )

    return AnalysisReport(
        ticker=ticker,
        vetoed=bool(item["vetoed"]),
        veto_reason=item.get("veto_reason") or None,
        bull_case=str(item["bull_case"]),
        bear_case=str(item["bear_case"]),
        data_gaps=[str(g) for g in item.get("data_gaps", [])],
        data_request=str(item.get("data_request") or ""),
        model_confidence=confidence,
        confidence_reason=str(item["confidence_reason"]),
        self_critique=str(item["self_critique"]),
        algorithm_feedback=str(item.get("algorithm_feedback") or ""),
    )


def _validate_all_tickers_covered(
    reports: list[AnalysisReport],
    candidates: list[ScoredTicker],
) -> None:
    returned = {r.ticker for r in reports}
    expected = {c.ticker for c in candidates}
    missing  = expected - returned
    if missing:
        raise ValueError(f"LLM response missing tickers: {missing}")


def _neutral_report(ticker: str) -> AnalysisReport:
    """Fallback when LLM fails — neutral, non-vetoing, clearly marked."""
    return AnalysisReport(
        ticker=ticker,
        vetoed=False,
        veto_reason=None,
        bull_case="LLM analysis unavailable — math score governs.",
        bear_case="LLM analysis unavailable — review manually.",
        data_gaps=["LLM call failed"],
        data_request="",
        model_confidence="low",
        confidence_reason="LLM call failed; this is a fallback report.",
        self_critique="No self-critique available due to LLM failure.",
        algorithm_feedback="",
    )


def _candidate_context(c: ScoredTicker) -> dict:
    """Build the per-ticker dict passed to the monthly LLM prompt."""
    ctx: dict = {
        "ticker":             c.ticker,
        "composite_score":    c.score,
        "factor_ranks": {
            "quality":        c.quality_rank,
            "value":          c.value_rank,
            "momentum":       c.momentum_rank,
            "profitability":  c.profitability_rank,
        },
    }
    if c.fundamentals is not None:
        f = c.fundamentals
        pe = round(1.0 / f.earnings_yield, 1) if f.earnings_yield and f.earnings_yield > 0 else None
        ctx["raw_metrics"] = {
            "roe_pct":            round(f.roe * 100, 1) if f.roe is not None else None,
            "fcf_margin_pct":     round(f.fcf_margin * 100, 1) if f.fcf_margin is not None else None,
            "debt_to_equity_pct": f.debt_to_equity,
            "pe_ratio":           pe,
            "momentum_12m_pct":   round(f.momentum_12_1 * 100, 1) if f.momentum_12_1 is not None else None,
            "revenue_growth_pct": round(f.revenue_growth * 100, 1) if f.revenue_growth is not None else None,
            "gross_profit_to_assets": f.gross_profit_to_assets,
            "market_cap_b_usd":   f.market_cap_b,
            "sector":             f.sector,
        }
    return ctx
