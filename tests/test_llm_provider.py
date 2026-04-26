"""Tests for core/llm_provider.py — LiteLLM wrapper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.llm_provider import call_llm


def _mock_response(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_call_llm_returns_string():
    with patch("litellm.completion", return_value=_mock_response("hello")):
        result = call_llm("prompt", "groq/llama-3.3-70b-versatile", 512)
    assert result == "hello"


def test_call_llm_passes_model_to_litellm():
    with patch("litellm.completion", return_value=_mock_response("x")) as mock_llm:
        call_llm("prompt", "groq/llama-3.3-70b-versatile", 512)
    assert mock_llm.call_args.kwargs["model"] == "groq/llama-3.3-70b-versatile"


def test_call_llm_passes_max_tokens():
    with patch("litellm.completion", return_value=_mock_response("x")) as mock_llm:
        call_llm("prompt", "any-model", 1024)
    assert mock_llm.call_args.kwargs["max_tokens"] == 1024


def test_call_llm_uses_fixed_temperature():
    with patch("litellm.completion", return_value=_mock_response("x")) as mock_llm:
        call_llm("prompt", "any-model", 512)
    assert mock_llm.call_args.kwargs["temperature"] == 0.1


def test_o_series_model_uses_temperature_1():
    """O-series reasoning models must use temperature=1 (they reject 0.1)."""
    with patch("litellm.completion", return_value=_mock_response("x")) as mock_llm:
        call_llm("prompt", "github_copilot/o3-mini", 512)
    assert mock_llm.call_args.kwargs["temperature"] == 1


def test_o1_model_uses_temperature_1():
    with patch("litellm.completion", return_value=_mock_response("x")) as mock_llm:
        call_llm("prompt", "openai/o1-mini", 512)
    assert mock_llm.call_args.kwargs["temperature"] == 1


def test_call_llm_raises_runtime_error_on_api_failure():
    with patch("litellm.completion", side_effect=Exception("API down")):
        with pytest.raises(RuntimeError, match="LLM call failed"):
            call_llm("prompt", "any-model", 512)


def test_call_llm_error_message_includes_model():
    with patch("litellm.completion", side_effect=Exception("timeout")):
        with pytest.raises(RuntimeError, match="groq/llama-3.3-70b-versatile"):
            call_llm("prompt", "groq/llama-3.3-70b-versatile", 512)


# ── GitHub Copilot routing ────────────────────────────────────────────────────

def test_copilot_model_sets_api_base():
    """github_copilot/ prefix must route to the Copilot API base URL."""
    with patch("litellm.completion", return_value=_mock_response("ok")) as mock_llm:
        call_llm("prompt", "github_copilot/gpt-4o", 512)
    assert mock_llm.call_args.kwargs["api_base"] == "https://models.inference.ai.azure.com"


def test_copilot_model_strips_prefix():
    """The openai/ prefix is added and the github_copilot/ prefix is stripped."""
    with patch("litellm.completion", return_value=_mock_response("ok")) as mock_llm:
        call_llm("prompt", "github_copilot/gpt-4o", 512)
    assert mock_llm.call_args.kwargs["model"] == "openai/gpt-4o"


def test_copilot_model_uses_github_token(monkeypatch):
    """GITHUB_TOKEN is passed as api_key for Copilot calls."""
    monkeypatch.setenv("GITHUB_TOKEN", "gh_test_token")
    with patch("litellm.completion", return_value=_mock_response("ok")) as mock_llm:
        call_llm("prompt", "github_copilot/claude-3.5-sonnet", 512)
    assert mock_llm.call_args.kwargs["api_key"] == "gh_test_token"


# ── call_llm_with_fallbacks ───────────────────────────────────────────────────

from core.llm_provider import call_llm_with_fallbacks


def test_fallbacks_returns_first_success():
    with patch("core.llm_provider.call_llm", return_value="result") as mock_call:
        out = call_llm_with_fallbacks("prompt", ["model-a", "model-b"], 512)
    assert out == "result"
    assert mock_call.call_count == 1


def test_fallbacks_tries_next_on_failure():
    calls = []

    def side_effect(prompt, model, max_tokens):
        calls.append(model)
        if model == "model-a":
            raise RuntimeError("model-a down")
        return "fallback result"

    with patch("core.llm_provider.call_llm", side_effect=side_effect):
        out = call_llm_with_fallbacks("prompt", ["model-a", "model-b"], 512)
    assert out == "fallback result"
    assert calls == ["model-a", "model-b"]


def test_fallbacks_raises_if_all_fail():
    with patch("core.llm_provider.call_llm", side_effect=RuntimeError("down")):
        with pytest.raises(RuntimeError, match="All LLM models failed"):
            call_llm_with_fallbacks("prompt", ["m1", "m2"], 512)
