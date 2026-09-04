"""OpenRouter reference pricing for LLM token-cost estimates.

Fetches OpenRouter's public (unauthenticated) models catalogue and maps this
repo's (provider, model) pairs onto OpenRouter model ids. Prices are USD
per token. These are REFERENCE prices only — actual billing depends on the
configured provider (e.g. the GLM Coding Plan is subscription quota, not
pay-per-token); the point is a consistent yardstick across models, and
real per-token cost for pay-per-token providers like OpenRouter.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

_CACHE_TTL_SEC = 3600

_cache: tuple[float, dict[str, dict[str, float | None]]] | None = None

# OpenRouter's vendor prefix for this repo's provider codes.
_PROVIDER_PREFIX = {"zai": "z-ai", "openai": "openai", "anthropic": "anthropic"}


def _per_token(v: Any) -> float | None:
    """OpenRouter prices are USD-per-token strings; '-1'/missing = unknown."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def load_prices() -> dict[str, dict[str, float | None]]:
    """model_id (lowercase) -> {prompt, completion, input_cache_read} USD/token
    rates. Raises on fetch failure; callers decide the fallback. Cached
    in-process for _CACHE_TTL_SEC."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SEC:
        return _cache[1]
    # Public, unauthenticated catalogue endpoint (fixed URL — no user input).
    resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
    resp.raise_for_status()
    prices: dict[str, dict[str, float | None]] = {}
    for m in resp.json().get("data", []):
        p = m.get("pricing") or {}
        prices[str(m.get("id", "")).lower()] = {
            "prompt": _per_token(p.get("prompt")),
            "completion": _per_token(p.get("completion")),
            "input_cache_read": _per_token(p.get("input_cache_read")),
        }
    _cache = (now, prices)
    return prices


def lookup(prices: dict[str, dict[str, float | None]],
           provider: str, model: str) -> dict[str, float | None] | None:
    """Map this repo's (provider, model) onto an OpenRouter id, if priced.
    Tries the bare model name first (covers openrouter-provider models whose
    ids already carry the vendor prefix, e.g. 'poolside/laguna-s-2.1:free'),
    then the provider-prefixed form."""
    candidates = [model.lower()]
    prefix = _PROVIDER_PREFIX.get(provider)
    if prefix:
        candidates.append(f"{prefix}/{model.lower()}")
    for c in candidates:
        if c in prices:
            return prices[c]
    return None


def cost_usd(price: dict[str, float | None], prompt_tokens: int,
             cached_tokens: int, completion_tokens: int) -> float:
    """Reference USD cost for one run's usage. Cached tokens bill at the
    cache-read rate when OpenRouter publishes one, otherwise at the plain
    input rate; unpriced fields make the estimate incomplete."""
    uncached = max(prompt_tokens - cached_tokens, 0)
    p, c, r = price.get("prompt"), price.get("completion"), price.get("input_cache_read")
    if p is None or c is None:
        raise ValueError("model lacks an OpenRouter input/output price")
    return (uncached * p
            + cached_tokens * (r if r is not None else p)
            + completion_tokens * c)
