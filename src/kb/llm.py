"""Multi-provider LLM client + JSON-schema structured extraction.

Supports four ways of turning (system, user, json_schema) into a parsed dict:

- ``openai``     — OpenAI (or any OpenAI-compatible endpoint via LLM_BASE_URL,
                    e.g. Azure OpenAI, local Ollama/llama.cpp).
- ``github``     — shells out to the local `copilot` CLI in non-interactive
                    prompt mode (``copilot -p ... --silent``). There is no
                    public raw completions API for Copilot itself, so this
                    drives the same CLI binary used interactively, with all
                    tool use disabled so it behaves like a plain chat call.
- ``anthropic``  — Anthropic Messages API, using a single forced tool call
                    (tool_choice) whose input_schema is the JSON schema, since
                    Anthropic has no native "strict JSON schema" response mode.
- ``zai``        — Z.ai (Zhipu GLM), which speaks the OpenAI wire format, so
                    it reuses the same client code path as ``openai``.

Every provider exposes the same ``chat_json(system, user, schema, provider,
model)`` signature so callers (see ``extract.py``) can run the exact same
prompt/schema through several providers and compare results. Embeddings only
make sense for OpenAI-wire-compatible providers, so ``embed()`` supports
``openai`` and ``zai`` only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from .config import settings
from .logging_setup import get_logger

log = get_logger("llm")

PROVIDERS: tuple[str, ...] = ("openai", "github", "anthropic", "zai")
EMBEDDING_PROVIDERS: tuple[str, ...] = ("openai", "zai")


class LLMError(RuntimeError):
    """Raised when a provider fails to produce a usable response."""


def default_model(provider: str) -> str:
    """The configured default model for a given provider."""
    s = settings()
    try:
        return {
            "openai": s.llm_model,
            "github": s.github_model,
            "anthropic": s.anthropic_model,
            "zai": s.zai_model,
        }[provider]
    except KeyError:
        raise ValueError(f"unknown LLM provider {provider!r}; choose one of {PROVIDERS}") from None


def has_credentials(provider: str) -> bool:
    """Best-effort check that a provider is configured enough to try calling."""
    s = settings()
    if provider == "openai":
        return bool(s.llm_api_key)
    if provider == "zai":
        return bool(s.zai_api_key)
    if provider == "anthropic":
        return bool(s.anthropic_api_key)
    if provider == "github":
        return True  # relies on local `copilot /login` state, not an env var
    return False


# ---------------------------------------------------------------------------
# openai / zai — both speak the OpenAI chat.completions wire format.
# ---------------------------------------------------------------------------

_openai_clients: dict[tuple[str, str], Any] = {}


def _openai_compatible_client(base_url: str, api_key: str):
    from openai import OpenAI

    key = (base_url, api_key)
    if key not in _openai_clients:
        _openai_clients[key] = OpenAI(api_key=api_key or "sk-noop", base_url=base_url)
    return _openai_clients[key]


def _chat_json_openai_compatible(system: str, user: str, schema: dict[str, Any],
                                  model: str, base_url: str, api_key: str) -> dict[str, Any]:
    client = _openai_compatible_client(base_url, api_key)
    resp = client.chat.completions.create(
        model=model,
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


def _embed_openai_compatible(texts: list[str], model: str, base_url: str,
                              api_key: str) -> list[list[float]]:
    client = _openai_compatible_client(base_url, api_key)
    resp = client.embeddings.create(model=model, input=texts)
    return [d.embedding for d in resp.data]


# ---------------------------------------------------------------------------
# anthropic — force structured output via a single required tool call.
# ---------------------------------------------------------------------------

_anthropic_client = None


def _anthropic_sdk_client(api_key: str, base_url: str):
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None)
    return _anthropic_client


def _chat_json_anthropic(system: str, user: str, schema: dict[str, Any], model: str,
                          api_key: str, base_url: str, max_tokens: int = 8192) -> dict[str, Any]:
    client = _anthropic_sdk_client(api_key, base_url)
    tool_name = "emit_structured_result"
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{
            "name": tool_name,
            "description": "Emit the extraction result. Always call this exactly once.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": tool_name},
        temperature=0.1,
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    raise LLMError("anthropic response did not include the expected tool_use block")


# ---------------------------------------------------------------------------
# github — shell out to the local `copilot` CLI (non-interactive prompt mode).
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

# Windows-only gotcha: npm installs `copilot` as `copilot.cmd`/`copilot.ps1`
# shim scripts. Windows runs `.cmd`/`.bat` files via an implicit `cmd.exe /c`
# wrapper, whose command-line buffer is capped at ~8191 characters -- far
# smaller than our prompts (schema + up to a 14,000-char article chunk is
# routinely 15-17KB). Passing a long -p prompt through the shim fails with
# `OSError: [WinError 206] The filename or extension is too long`. The native
# `copilot.exe` (e.g. installed via `winget install GitHub.Copilot`) is a real
# executable with no such wrapper -- CreateProcess's own limit is ~32K chars,
# comfortably large enough -- so prefer it over the shim when both exist.
_resolved_cli_paths: dict[str, str] = {}


def _resolve_cli_path(cli_path: str) -> str:
    """Best-effort resolution of the configured CLI path to the native
    executable, to sidestep the Windows cmd-shim command-line length limit
    described above. Falls back to the configured value unchanged (letting
    subprocess/PATH resolution handle it as before) if nothing better is found.
    """
    if cli_path in _resolved_cli_paths:
        return _resolved_cli_paths[cli_path]
    resolved = cli_path
    if os.name == "nt" and os.sep not in cli_path and "/" not in cli_path:
        # Ask specifically for the literal "<name>.exe" so PATHEXT-based
        # per-directory resolution doesn't stop early at a .cmd/.ps1 shim
        # that happens to live earlier on PATH than the real .exe.
        exe = shutil.which(cli_path if cli_path.lower().endswith(".exe") else f"{cli_path}.exe")
        if exe:
            resolved = exe
    _resolved_cli_paths[cli_path] = resolved
    return resolved


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Pull a single JSON object out of CLI stdout, tolerating stray fences/prose."""
    stripped = _FENCE_RE.sub("", raw).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start == -1:
        raise LLMError(f"no JSON object found in `copilot` CLI output: {stripped[:300]!r}")
    depth = 0
    for i, ch in enumerate(stripped[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start:i + 1])
    raise LLMError(f"unbalanced JSON object in `copilot` CLI output: {stripped[:300]!r}")


def _chat_json_copilot_cli(system: str, user: str, schema: dict[str, Any], model: str,
                            cli_path: str, timeout_sec: int) -> dict[str, Any]:
    prompt = (
        f"{system}\n\n"
        "Respond with ONLY a single raw JSON object as your entire reply -- no "
        "markdown code fences, no commentary before or after -- that strictly "
        f"matches this JSON Schema:\n{json.dumps(schema)}\n\n{user}"
    )
    resolved_path = _resolve_cli_path(cli_path)
    cmd = [
        resolved_path, "-p", prompt,
        "--silent",              # print only the final response, no stats/banner
        "--allow-all-tools",     # required for non-interactive mode
        "--available-tools=",    # ...but make no tools available, so it can't use any
        "--no-ask-user",
        "--no-color",
    ]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"`copilot` CLI timed out after {timeout_sec}s") from exc
    except OSError as exc:
        # Windows raises this (as FileNotFoundError, an OSError subclass --
        # check winerror before the more specific isinstance check below) when
        # the built command line is too long for CreateProcess: either the
        # ~8191-char cmd.exe wrapper limit (only the npm .cmd/.ps1 shim is on
        # PATH) or, more rarely, the ~32K CreateProcess limit itself for a
        # very large prompt. `extract._chunks()` caps chunks at max_chars
        # precisely to avoid the latter; if you see this, something upstream
        # is passing an oversized chunk/schema.
        if os.name == "nt" and getattr(exc, "winerror", None) == 206:
            raise LLMError(
                f"`copilot` CLI invocation via {resolved_path!r} exceeded the Windows "
                "command-line length limit. If only the npm .cmd/.ps1 shim is on PATH, "
                "install the native binary with `winget install GitHub.Copilot` (it is "
                "preferred automatically once on PATH) to raise the limit from ~8191 to "
                "~32K characters; if it persists even with the native .exe, the prompt "
                "itself is too large -- lower the extraction chunk size."
            ) from exc
        if isinstance(exc, FileNotFoundError):
            raise LLMError(f"`copilot` CLI not found at {cli_path!r}; is it installed on PATH?") from exc
        raise LLMError(f"`copilot` CLI invocation failed: {exc}") from exc
    if proc.returncode != 0:
        raise LLMError(f"`copilot` CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return _extract_json_object(proc.stdout)




# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30),
       retry=retry_if_not_exception_type(ValueError))
def chat_json(system: str, user: str, schema: dict[str, Any],
              provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Call an LLM with a JSON schema; return the parsed structured dict.

    ``provider`` defaults to ``settings().llm_provider``; ``model`` defaults to
    that provider's configured model (see ``default_model``).
    """
    s = settings()
    provider = provider or s.llm_provider
    if provider not in PROVIDERS:
        raise ValueError(f"unknown LLM provider {provider!r}; choose one of {PROVIDERS}")
    model = model or default_model(provider)

    if provider == "openai":
        return _chat_json_openai_compatible(system, user, schema, model, s.llm_base_url, s.llm_api_key)
    if provider == "zai":
        return _chat_json_openai_compatible(system, user, schema, model, s.zai_base_url, s.zai_api_key)
    if provider == "anthropic":
        return _chat_json_anthropic(system, user, schema, model, s.anthropic_api_key, s.anthropic_base_url)
    if provider == "github":
        return _chat_json_copilot_cli(system, user, schema, model, s.github_cli_path, s.github_cli_timeout_sec)
    raise AssertionError(provider)  # pragma: no cover — guarded by the check above


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30),
       retry=retry_if_not_exception_type(ValueError))
def embed(texts: list[str], provider: str | None = None, model: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    s = settings()
    provider = provider or s.llm_embedding_provider
    if provider not in EMBEDDING_PROVIDERS:
        raise ValueError(
            f"embeddings are not supported for provider {provider!r}; "
            f"use one of {EMBEDDING_PROVIDERS} (set LLM_EMBEDDING_PROVIDER)"
        )
    if provider == "openai":
        return _embed_openai_compatible(texts, model or s.llm_embedding_model, s.llm_base_url, s.llm_api_key)
    return _embed_openai_compatible(texts, model or s.zai_embedding_model, s.zai_base_url, s.zai_api_key)


# Backwards-compatible alias: older code imported `client()` for direct
# OpenAI SDK access. Kept for anything outside this module that still does.
def client():
    s = settings()
    return _openai_compatible_client(s.llm_base_url, s.llm_api_key)
