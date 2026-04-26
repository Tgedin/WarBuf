"""LiteLLM wrapper. One function, one concern: call a model, return text."""
from __future__ import annotations

import os

import litellm

# GitHub Models — the public inference API included with any GitHub Copilot subscription.
# Works with a standard GitHub PAT (classic or fine-grained, no special scope needed).
# Endpoint: https://models.inference.ai.azure.com
# Set GITHUB_TOKEN in .env. Use "github_copilot/<model>" in rules.yaml.
# Available models: gpt-4o, gpt-4o-mini, claude-3.5-sonnet, claude-3.7-sonnet, o3-mini
_COPILOT_PREFIX   = "github_copilot/"
_COPILOT_API_BASE = "https://models.inference.ai.azure.com"

# O-series reasoning models don't support temperature != 1.
# Detect by model name suffix (o1, o3, o3-mini, o1-mini, etc.).
def _is_o_series(model: str) -> bool:
    name = model.split("/")[-1].lower()
    return name.startswith("o1") or name.startswith("o3")


def call_llm(prompt: str, model: str, max_tokens: int) -> str:
    """
    Call any LiteLLM-supported model. Provider is inferred from the model string.

    GitHub Copilot models: prefix with "github_copilot/" (e.g. "github_copilot/gpt-4o").
    Raises RuntimeError on API failure with model and cause in the message.
    Temperature is fixed at 0.1; omitted for o-series models (they require temperature=1).
    """
    temperature = 1 if _is_o_series(model) else 0.1
    try:
        if model.startswith(_COPILOT_PREFIX):
            real_model = "openai/" + model[len(_COPILOT_PREFIX):]
            response = litellm.completion(
                model=real_model,
                api_base=_COPILOT_API_BASE,
                api_key=os.environ.get("GITHUB_TOKEN", ""),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        else:
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return response.choices[0].message.content
    except Exception as exc:
        raise RuntimeError(f"LLM call failed (model={model}): {exc}") from exc


def call_llm_with_fallbacks(prompt: str, models: list[str], max_tokens: int) -> str:
    """
    Try each model in order, return the first successful response.

    Raises RuntimeError only if every model in the list fails.
    """
    last_exc: Exception | None = None
    for model in models:
        try:
            return call_llm(prompt, model, max_tokens)
        except RuntimeError as exc:
            print(f"[LLM] {model} failed, trying next fallback: {exc}")
            last_exc = exc
    raise RuntimeError(f"All LLM models failed. Last error: {last_exc}") from last_exc
