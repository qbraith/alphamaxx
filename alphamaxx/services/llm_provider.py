"""Shared, optional LLM provider plumbing.

The rest of AlphaMaxx should depend on this small interface rather than
constructing provider SDK clients directly.  Provider imports stay lazy, and
an unconfigured key is a normal local-only state: ``try_generate_text``
returns ``None`` without importing an SDK or making a network request.
"""

from __future__ import annotations

import logging
from typing import Any

from alphamaxx.config import settings

log = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = frozenset({"gemini", "anthropic", "openai"})


def provider_name() -> str:
    """Return the normalized configured provider name."""
    return (settings.AI_ENGINE or "gemini").strip().lower()


def provider_api_key(provider: str | None = None) -> str:
    """Return the configured provider key, or an empty string.

    Unknown providers are treated as unconfigured here so UI capability checks
    remain safe. ``generate_text`` still raises a useful error for them.
    """
    name = (provider or provider_name()).strip().lower()
    return {
        "gemini": settings.GEMINI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }.get(name, "")


def is_configured(provider: str | None = None) -> bool:
    """Whether the selected provider has a non-empty API key."""
    return bool(provider_api_key(provider))


def generate_text(
    prompt: str,
    *,
    system: str,
    max_tokens: int = 600,
    temperature: float = 0.4,
    response_mime_type: str | None = None,
    response_schema: Any | None = None,
    thinking_budget: int | None = None,
) -> str:
    """Generate text with the configured provider.

    ``response_mime_type`` and ``response_schema`` are supported by Gemini for
    schema-constrained responses. Other providers reject these options
    explicitly instead of silently pretending their output was constrained.
    Callers remain responsible for parsing and domain-validating JSON.
    """
    name = provider_name()
    if name not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unknown AI_ENGINE: {name!r}")
    if not is_configured(name):
        raise RuntimeError(f"{name.upper()} API key is not set.")

    if name == "gemini":
        return _gemini(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            thinking_budget=thinking_budget,
        )
    if (
        response_mime_type is not None
        or response_schema is not None
        or thinking_budget is not None
    ):
        raise ValueError(
            f"Structured response constraints/provider controls are not "
            f"supported for {name!r}."
        )
    if name == "anthropic":
        return _anthropic(
            prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )
    return _openai(
        prompt, system=system, max_tokens=max_tokens, temperature=temperature
    )


def try_generate_text(
    prompt: str,
    *,
    system: str,
    max_tokens: int = 600,
    temperature: float = 0.4,
    response_mime_type: str | None = None,
    response_schema: Any | None = None,
    thinking_budget: int | None = None,
) -> str | None:
    """Best-effort generation for optional AI features.

    No key returns ``None`` immediately. Provider/import/network failures are
    logged and also return ``None``, leaving deterministic local behavior
    available.
    """
    if not is_configured():
        return None
    try:
        return generate_text(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            thinking_budget=thinking_budget,
        )
    except Exception as exc:
        log.warning("Optional %s generation failed: %s", provider_name(), exc)
        return None


def _gemini(
    prompt: str,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
    response_mime_type: str | None,
    response_schema: Any | None,
    thinking_budget: int | None,
) -> str:
    from google import genai
    from google.genai import types

    config_kwargs: dict[str, Any] = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_mime_type is not None:
        config_kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_budget=thinking_budget
        )

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return (response.text or "").strip()


def _anthropic(
    prompt: str,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


def _openai(
    prompt: str,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()
