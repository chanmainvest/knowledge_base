"""LLM client (OpenAI-compatible) + JSON-schema extraction."""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    s = settings()
    if _client is None:
        _client = OpenAI(api_key=s.llm_api_key or "sk-noop", base_url=s.llm_base_url)
    return _client


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30))
def chat_json(system: str, user: str, schema: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Call the chat model with a JSON schema; return parsed dict."""
    s = settings()
    resp = client().chat.completions.create(
        model=model or s.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "kb_extract", "schema": schema, "strict": True},
        },
        temperature=0.1,
    )
    return json.loads(resp.choices[0].message.content or "{}")


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30))
def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    s = settings()
    if not texts:
        return []
    resp = client().embeddings.create(model=model or s.llm_embedding_model, input=texts)
    return [d.embedding for d in resp.data]
