"""Shared optional-LLM plumbing stays local when no provider key is set."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alphamaxx.services import llm_provider


def _settings(**overrides):
    values = {
        "AI_ENGINE": "gemini",
        "GEMINI_API_KEY": "",
        "GEMINI_MODEL": "gemini-test",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_MODEL": "anthropic-test",
        "OPENAI_API_KEY": "",
        "OPENAI_MODEL": "openai-test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_no_key_fallback_does_not_call_provider(monkeypatch):
    monkeypatch.setattr(llm_provider, "settings", _settings())
    monkeypatch.setattr(
        llm_provider,
        "generate_text",
        lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
    )

    assert llm_provider.is_configured() is False
    assert llm_provider.try_generate_text("hello", system="system") is None


def test_provider_failure_is_best_effort(monkeypatch):
    monkeypatch.setattr(
        llm_provider, "settings", _settings(GEMINI_API_KEY="configured")  # pragma: allowlist secret
    )

    def fail(*_args, **_kwargs):
        raise OSError("provider unavailable")

    monkeypatch.setattr(llm_provider, "generate_text", fail)
    assert llm_provider.try_generate_text("hello", system="system") is None


def test_unknown_engine_is_safe_for_ui_and_explicit_for_generation(monkeypatch):
    monkeypatch.setattr(
        llm_provider, "settings", _settings(AI_ENGINE="not-a-provider")
    )
    assert llm_provider.is_configured() is False
    with pytest.raises(ValueError, match="Unknown AI_ENGINE"):
        llm_provider.generate_text("hello", system="system")


def test_structured_constraints_rejected_for_unsupported_provider(monkeypatch):
    monkeypatch.setattr(
        llm_provider,
        "settings",
        _settings(AI_ENGINE="openai", OPENAI_API_KEY="configured"),  # pragma: allowlist secret
    )
    with pytest.raises(ValueError, match="Structured response constraints"):
        llm_provider.generate_text(
            "hello",
            system="system",
            response_mime_type="application/json",
            response_schema={"type": "object"},
        )
