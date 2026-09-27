"""TypeSafe Jev classification for article and transcript content.

Jev answers typed questions rather than generating text. Keep this question
set versioned independently of the LLM prompt/schema registry.
"""
from __future__ import annotations

import httpx
import ssl

from .config import settings

VERSION = "jev-v1"

_QUESTIONS = {
    "is_marketing": {"type": "noul", "instructions": "Is this post predominantly a marketing pitch or promotion, rather than substantive content? A brief call to subscribe within substantial analysis does not count."},
    "is_advertisement": {"type": "noul", "instructions": "Is this post predominantly an advertisement or paid sponsor message?"},
    "no_real_content": {"type": "noul", "instructions": "Does this post lack substantive informational content, consisting mainly of a teaser, boilerplate, or call to action?"},
    "mentions_book": {"type": "noul", "instructions": "Does the content mention or discuss a book, including a book whose title is not given?"},
    "mentions_movie": {"type": "noul", "instructions": "Does the content mention or discuss a movie or documentary, including one whose title is not given?"},
    "stocks": {"type": "noul", "instructions": "Does the content discuss stocks or equity markets as an asset class?"},
    "bonds": {"type": "noul", "instructions": "Does the content discuss bonds or fixed income as an asset class?"},
    "foreign_exchange": {"type": "noul", "instructions": "Does the content discuss currencies or foreign exchange as an asset class?"},
    "commodities": {"type": "noul", "instructions": "Does the content discuss commodities, including precious metals or energy, as an asset class?"},
    "crypto": {"type": "noul", "instructions": "Does the content discuss cryptocurrencies or digital assets as an asset class?"},
    "real_estate": {"type": "noul", "instructions": "Does the content discuss real estate or REITs as an asset class?"},
    "stock_us": {"type": "noul", "instructions": "Does the content discuss the United States stock market or US-listed stocks?"},
    "stock_hong_kong": {"type": "noul", "instructions": "Does the content discuss the Hong Kong stock market or Hong Kong-listed stocks?"},
    "stock_mainland_china": {"type": "noul", "instructions": "Does the content discuss mainland China's stock markets or mainland-listed A-shares?"},
    "stock_japan": {"type": "noul", "instructions": "Does the content discuss the Japanese stock market or Japan-listed stocks?"},
    "stock_europe": {"type": "noul", "instructions": "Does the content discuss European stock markets or Europe-listed stocks?"},
    "stock_india": {"type": "noul", "instructions": "Does the content discuss the Indian stock market or India-listed stocks?"},
    "stock_taiwan": {"type": "noul", "instructions": "Does the content discuss the Taiwan stock market or Taiwan-listed stocks?"},
    "stock_south_korea": {"type": "noul", "instructions": "Does the content discuss the South Korean stock market or Korea-listed stocks?"},
    "stock_other": {"type": "noul", "instructions": "Does the content discuss a specific stock market outside the US, Hong Kong, mainland China, Japan, Europe, India, Taiwan, and South Korea?"},
}


def _tls_context() -> ssl.SSLContext:
    """Use OS trust roots without inheriting SSLKEYLOGFILE.

    On this Windows runtime ssl.create_default_context() aborts inside
    OpenSSL when SSLKEYLOGFILE points at an inaccessible path. HTTPX's
    bundled CA set also misses a locally trusted issuer. Building the
    context directly preserves certificate verification and OS trust roots.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    return context


def classify(text: str, *, model: str | None = None) -> tuple[dict, dict]:
    """Return decisions with probabilities and total API token usage.

    Long content uses the existing extractor's paragraph-aware chunker. A
    positive mention in any chunk counts; whole-post promotional labels need
    a majority of chunks, matching the existing marketing-flag semantics.
    """
    from .extract import _chunks

    s = settings()
    if not s.jev_api_key:
        raise ValueError("JEV_API_KEY is required for Jev extraction")
    model = model or s.jev_model
    chunks = _chunks(text)
    responses = []
    tokens = {"input_tokens": 0, "output_tokens": 0}
    with httpx.Client(timeout=90, verify=_tls_context(), trust_env=False) as client:
        for chunk in chunks:
            response = client.post(
                f"{s.jev_base_url.rstrip('/')}/v1/systemone",
                headers={"Authorization": f"Bearer {s.jev_api_key}"},
                json={"model": model, "state": chunk, "questions": _QUESTIONS},
            )
            response.raise_for_status()
            data = response.json()
            answers = data["answers"]
            if set(answers) != set(_QUESTIONS):
                raise ValueError("Jev response omitted or added question answers")
            probabilities = {}
            for name, answer in answers.items():
                value = answer.get("noul")
                if answer.get("type") != "noul" or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError(f"Invalid Jev answer for {name}")
                probabilities[name] = float(value)
            responses.append(probabilities)
            for key in tokens:
                tokens[key] += int(data.get("usage", {}).get(key, 0))
    whole_post = {"is_marketing", "is_advertisement", "no_real_content"}
    probabilities = {
        name: (sum(row[name] for row in responses) / len(responses)
               if name in whole_post else max(row[name] for row in responses))
        for name in _QUESTIONS
    }
    return {
        "decisions": {name: value > 0.5 if name in whole_post else value >= 0.5
                      for name, value in probabilities.items()},
        "probabilities": probabilities,
        "chunk_probabilities": responses,
    }, tokens
