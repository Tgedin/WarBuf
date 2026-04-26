"""
Tests for core/agent.py — the LLM analysis layer.

Strategy: mock call_llm to return controlled JSON so we test all
parsing, validation, and fail-open paths without real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from core.agent import (
    AnalysisReport,
    analyse_candidates,
    analyse_weekly,
    _neutral_report,
    _build_memory_block,
)
from core.scorer import ScoredTicker


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_candidate(ticker: str, score: float = 0.75) -> ScoredTicker:
    return ScoredTicker(
        ticker=ticker,
        score=score,
        quality_rank=0.8,
        value_rank=0.7,
        momentum_rank=0.6,
        profitability_rank=0.9,
    )


def _valid_report_json(
    ticker: str,
    vetoed: bool = False,
    confidence: str = "high",
) -> dict:
    return {
        "ticker":             ticker,
        "vetoed":             vetoed,
        "veto_reason":        "active fraud confirmed" if vetoed else None,
        "bull_case":          "Strong FCF, dominant moat.",
        "bear_case":          "EU regulatory risk.",
        "data_gaps":          ["earnings call transcript"],
        "data_request":       "FCF for last 3 years",
        "model_confidence":   confidence,
        "confidence_reason":  "Fundamentals consistent, headlines benign.",
        "self_critique":      "I may anchor on historical performance.",
        "algorithm_feedback": "Momentum rank 0.9 may reflect a one-off event; consider capping at 0.8 for large-caps.",
    }


# ── Happy path ────────────────────────────────────────────────────────────────

def _analyse_single(ticker: str, **report_kwargs) -> "AnalysisReport":
    """Run analyse_candidates with one candidate, return the single report."""
    candidates = [_make_candidate(ticker)]
    llm_response = json.dumps([_valid_report_json(ticker, **report_kwargs)])
    with patch("core.agent.call_llm", return_value=llm_response):
        (report,) = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    return report


def test_report_count_matches_candidate_count():
    candidates = [_make_candidate("AAPL"), _make_candidate("MSFT")]
    llm_response = json.dumps([_valid_report_json("AAPL"), _valid_report_json("MSFT")])
    with patch("core.agent.call_llm", return_value=llm_response):
        reports = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert len(reports) == 2


def test_report_tickers_match_candidates():
    candidates = [_make_candidate("AAPL"), _make_candidate("MSFT")]
    llm_response = json.dumps([_valid_report_json("AAPL"), _valid_report_json("MSFT")])
    with patch("core.agent.call_llm", return_value=llm_response):
        reports = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert {r.ticker for r in reports} == {"AAPL", "MSFT"}


def test_confirmed_candidate_is_not_vetoed():
    assert _analyse_single("AAPL").vetoed is False


def test_confirmed_candidate_has_no_veto_reason():
    assert _analyse_single("AAPL").veto_reason is None


def test_confirmed_candidate_has_bull_case():
    assert _analyse_single("AAPL").bull_case != ""


def test_confirmed_candidate_has_bear_case():
    assert _analyse_single("AAPL").bear_case != ""


def test_confirmed_candidate_data_gaps_is_list():
    assert isinstance(_analyse_single("AAPL").data_gaps, list)


def test_confirmed_candidate_has_confidence_reason():
    assert _analyse_single("AAPL").confidence_reason != ""


def test_confirmed_candidate_has_self_critique():
    assert _analyse_single("AAPL").self_critique != ""


def test_vetoed_candidate_flag_is_true():
    assert _analyse_single("META", vetoed=True).vetoed is True


def test_vetoed_candidate_has_reason():
    assert _analyse_single("META", vetoed=True).veto_reason == "active fraud confirmed"


def test_confidence_high_accepted():
    assert _analyse_single("AAPL", confidence="high").model_confidence == "high"


def test_confidence_medium_accepted():
    assert _analyse_single("AAPL", confidence="medium").model_confidence == "medium"


def test_confidence_low_accepted():
    assert _analyse_single("AAPL", confidence="low").model_confidence == "low"


def test_to_dict_has_all_expected_keys():
    d = _analyse_single("NVDA").to_dict()
    expected_keys = {
        "ticker", "vetoed", "veto_reason", "bull_case", "bear_case",
        "data_gaps", "data_request", "model_confidence", "confidence_reason",
        "self_critique", "algorithm_feedback",
    }
    assert set(d.keys()) == expected_keys


def test_algorithm_feedback_field_present():
    assert _analyse_single("AAPL").algorithm_feedback != ""


def test_data_request_field_present():
    assert _analyse_single("AAPL").data_request == "FCF for last 3 years"


def test_data_request_defaults_empty_on_missing_field():
    candidates = [_make_candidate("AAPL")]
    report_json = _valid_report_json("AAPL")
    del report_json["data_request"]
    with patch("core.agent.call_llm", return_value=json.dumps([report_json])):
        (report,) = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert report.data_request == ""


# ── Weekly veto path ──────────────────────────────────────────────────────────

def _weekly_veto_json(ticker: str, vetoed: bool = False) -> dict:
    return {"ticker": ticker, "vetoed": vetoed, "veto_reason": "fraud confirmed" if vetoed else None}


def test_weekly_returns_one_report_per_candidate():
    candidates = [_make_candidate("AAPL"), _make_candidate("MSFT")]
    raw = json.dumps([_weekly_veto_json("AAPL"), _weekly_veto_json("MSFT")])
    with patch("core.agent.call_llm", return_value=raw):
        reports = analyse_weekly(candidates, {}, model="any", max_tokens=256)
    assert len(reports) == 2


def test_weekly_report_not_vetoed_by_default():
    candidates = [_make_candidate("AAPL")]
    raw = json.dumps([_weekly_veto_json("AAPL", vetoed=False)])
    with patch("core.agent.call_llm", return_value=raw):
        (report,) = analyse_weekly(candidates, {}, model="any", max_tokens=256)
    assert report.vetoed is False


def test_weekly_veto_flag_propagated():
    candidates = [_make_candidate("META")]
    raw = json.dumps([_weekly_veto_json("META", vetoed=True)])
    with patch("core.agent.call_llm", return_value=raw):
        (report,) = analyse_weekly(candidates, {}, model="any", max_tokens=256)
    assert report.vetoed is True


def test_weekly_fail_open_on_exception():
    candidates = [_make_candidate("AAPL")]
    with patch("core.agent.call_llm", side_effect=RuntimeError("timeout")):
        reports = analyse_weekly(candidates, {}, model="any", max_tokens=256)
    assert len(reports) == 1 and not reports[0].vetoed


def test_weekly_empty_candidates_returns_empty():
    with patch("core.agent.call_llm") as mock_llm:
        reports = analyse_weekly([], {}, model="any", max_tokens=256)
    assert reports == []
    mock_llm.assert_not_called()


# ── Memory block ──────────────────────────────────────────────────────────────

def test_build_memory_block_no_history():
    candidates = [_make_candidate("AAPL")]
    result = _build_memory_block({}, candidates)
    assert result == "No prior analyses on record."


def test_build_memory_block_contains_ticker():
    candidates = [_make_candidate("MSFT")]
    history = [{"date": "2025-01-01", "action": "BUY", "score": 0.8,
                "bull_case": "cloud growth", "bear_case": "competition",
                "self_critique": "may be anchored"}]
    result = _build_memory_block({"MSFT": history}, candidates)
    assert "MSFT" in result
    assert "BUY" in result


def test_build_memory_block_no_prior_for_ticker():
    candidates = [_make_candidate("GOOGL")]
    # AAPL has history but GOOGL does not — per-ticker "no prior analysis" line expected
    result = _build_memory_block({"AAPL": []}, candidates)
    assert "no prior analysis" in result


# ── Monthly memory injection ──────────────────────────────────────────────────

def test_monthly_with_prior_decisions_passes_through():
    """analyse_candidates must accept prior_decisions without error."""
    candidates = [_make_candidate("AAPL")]
    prior = {"AAPL": [{"date": "2025-01-01", "action": "BUY", "score": 0.75,
                        "bull_case": "moat", "bear_case": "risk",
                        "self_critique": "anchored"}]}
    llm_json = json.dumps([_valid_report_json("AAPL")])
    with patch("core.agent.call_llm", return_value=llm_json):
        reports = analyse_candidates(
            candidates, {}, model="any", max_tokens=512, prior_decisions=prior
        )
    assert len(reports) == 1 and reports[0].ticker == "AAPL"


def test_monthly_with_rules_context_included_in_prompt():
    """rules_context is formatted into the explore prompt without error."""
    candidates = [_make_candidate("MSFT")]
    rules = {
        "factor_weights": {"quality": 0.35, "value": 0.25, "momentum": 0.25, "profitability": 0.15},
        "min_market_cap_B": 5, "max_pe_ratio": 40, "min_revenue_growth_pct": 5,
        "max_debt_to_equity": 150, "sectors_excluded": [],
        "max_positions": 5, "max_position_pct": 15, "min_position_eur": 300,
        "cash_floor_pct": 10, "stop_loss_pct": 15, "score_collapse_delta": 0.25,
        "min_hold_months": 6, "macro_guard": {"sma_days": 200},
    }
    llm_json = json.dumps([_valid_report_json("MSFT")])
    captured: list[str] = []

    def capture_prompt(prompt, model, max_tokens):
        captured.append(prompt)
        return llm_json

    with patch("core.agent.call_llm", side_effect=capture_prompt):
        reports = analyse_candidates(
            candidates, {}, model="any", max_tokens=512, rules_context=rules
        )
    assert reports[0].ticker == "MSFT"
    assert "quality=0.35" in captured[0]
    assert "max_PE=40" in captured[0]


def test_fallback_models_used_on_primary_failure():
    """fallback_models param: second model is called when first fails."""
    candidates = [_make_candidate("AAPL")]
    llm_json = json.dumps([_valid_report_json("AAPL")])
    call_log = []

    def side_effect(prompt, model, max_tokens):
        call_log.append(model)
        if model == "primary":
            raise RuntimeError("primary down")
        return llm_json

    with patch("core.agent.call_llm", side_effect=side_effect):
        reports = analyse_candidates(
            candidates, {}, model="primary", max_tokens=512,
            fallback_models=["fallback-1"],
        )
    assert "fallback-1" in call_log
    assert len(reports) == 1


def test_surrounding_prose_stripped_from_llm_response():
    candidates = [_make_candidate("AMZN")]
    raw = "Here is my analysis:\n" + json.dumps([_valid_report_json("AMZN")]) + "\nDone."
    with patch("core.agent.call_llm", return_value=raw):
        (report,) = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert report.ticker == "AMZN"


def test_empty_candidates_returns_empty_list():
    with patch("core.agent.call_llm") as mock_llm:
        reports = analyse_candidates([], {}, model="any", max_tokens=512)
    assert reports == []


def test_empty_candidates_does_not_call_llm():
    with patch("core.agent.call_llm") as mock_llm:
        analyse_candidates([], {}, model="any", max_tokens=512)
    mock_llm.assert_not_called()


# ── Fail-open (LLM failure) ───────────────────────────────────────────────────

def _fail_open_reports(side_effect=None, return_value=None) -> list:
    candidates = [_make_candidate("AAPL"), _make_candidate("MSFT")]
    if side_effect is not None:
        with patch("core.agent.call_llm", side_effect=side_effect):
            return analyse_candidates(candidates, {}, model="any", max_tokens=512)
    with patch("core.agent.call_llm", return_value=return_value):
        return analyse_candidates(candidates, {}, model="any", max_tokens=512)


def test_fail_open_returns_all_candidates_on_exception():
    reports = _fail_open_reports(side_effect=RuntimeError("API down"))
    assert len(reports) == 2


def test_fail_open_does_not_veto_on_exception():
    reports = _fail_open_reports(side_effect=RuntimeError("API down"))
    assert all(not r.vetoed for r in reports)


def test_fail_open_marks_low_confidence_on_exception():
    reports = _fail_open_reports(side_effect=RuntimeError("API down"))
    assert all(r.model_confidence == "low" for r in reports)


def test_fail_open_on_bad_json_returns_one_report():
    reports = _fail_open_reports(return_value="not json at all")
    assert len(reports) == 2


def test_fail_open_on_bad_json_does_not_veto():
    reports = _fail_open_reports(return_value="not json at all")
    assert all(not r.vetoed for r in reports)


def test_fail_open_on_missing_ticker_returns_all_candidates():
    candidates = [_make_candidate("AAPL"), _make_candidate("MSFT")]
    llm_response = json.dumps([_valid_report_json("AAPL")])  # MSFT missing
    with patch("core.agent.call_llm", return_value=llm_response):
        reports = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert len(reports) == 2


def test_fail_open_on_missing_ticker_uses_low_confidence():
    candidates = [_make_candidate("AAPL"), _make_candidate("MSFT")]
    llm_response = json.dumps([_valid_report_json("AAPL")])  # MSFT missing
    with patch("core.agent.call_llm", return_value=llm_response):
        reports = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert all(r.model_confidence == "low" for r in reports)


def test_fail_open_on_invalid_confidence_uses_low_confidence():
    candidates = [_make_candidate("AAPL")]
    bad = _valid_report_json("AAPL")
    bad["model_confidence"] = "extreme"
    with patch("core.agent.call_llm", return_value=json.dumps([bad])):
        (report,) = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert report.model_confidence == "low"


def test_fail_open_on_unknown_ticker_returns_original_candidate():
    candidates = [_make_candidate("AAPL")]
    with patch("core.agent.call_llm", return_value=json.dumps([_valid_report_json("TSLA")])):
        (report,) = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert report.ticker == "AAPL"


def test_fail_open_on_unknown_ticker_uses_low_confidence():
    candidates = [_make_candidate("AAPL")]
    with patch("core.agent.call_llm", return_value=json.dumps([_valid_report_json("TSLA")])):
        (report,) = analyse_candidates(candidates, {}, model="any", max_tokens=512)
    assert report.model_confidence == "low"


# ── Neutral report helper ─────────────────────────────────────────────────────

def test_neutral_report_is_not_vetoed():
    r = _neutral_report("XYZ")
    assert r.ticker == "XYZ"
    assert r.vetoed is False
    assert r.model_confidence == "low"


def test_neutral_report_data_request_is_empty():
    r = _neutral_report("XYZ")
    assert r.data_request == ""
