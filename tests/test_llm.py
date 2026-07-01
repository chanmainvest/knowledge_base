"""Unit tests for the multi-provider LLM client (no network/DB access)."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from kb import llm


def test_chat_json_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown LLM provider"):
        llm.chat_json("sys", "usr", {}, provider="not-a-provider")


def test_embed_rejects_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="not supported"):
        llm.embed(["hello"], provider="anthropic")


def test_embed_short_circuits_on_empty_input() -> None:
    assert llm.embed([]) == []


def test_default_model_known_providers() -> None:
    for provider in llm.PROVIDERS:
        # Should not raise; every provider has a configured default model field
        # (may be an empty string, e.g. github's "let the CLI decide").
        llm.default_model(provider)
    with pytest.raises(ValueError):
        llm.default_model("nope")


# ---------------------------------------------------------------------------
# _extract_json_object: robust JSON parsing of `copilot` CLI stdout
# ---------------------------------------------------------------------------

def test_extract_json_object_plain() -> None:
    assert llm._extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_strips_markdown_fences() -> None:
    raw = "```json\n{\"a\": 1, \"b\": [1, 2]}\n```"
    assert llm._extract_json_object(raw) == {"a": 1, "b": [1, 2]}


def test_extract_json_object_finds_balanced_braces_amid_prose() -> None:
    raw = 'Sure, here you go:\n{"a": {"nested": 1}, "b": 2}\nHope that helps!'
    assert llm._extract_json_object(raw) == {"a": {"nested": 1}, "b": 2}


def test_extract_json_object_raises_on_garbage() -> None:
    with pytest.raises(llm.LLMError):
        llm._extract_json_object("no json here at all")


# ---------------------------------------------------------------------------
# github provider: shells out to `copilot`; mock subprocess.run so tests don't
# actually invoke the CLI.
# ---------------------------------------------------------------------------

def test_chat_json_copilot_cli_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cmd = {}

    def fake_run(cmd, capture_output, text, encoding, timeout):  # noqa: ANN001
        captured_cmd["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    # Resolution to a native .exe (see `_resolve_cli_path`) is
    # environment-dependent (only matters on Windows, only when one is
    # actually installed) -- force the "nothing better found" path so this
    # test's assertions are deterministic across machines/OSes.
    monkeypatch.setattr(llm.shutil, "which", lambda *a, **k: None)
    llm._resolved_cli_paths.clear()
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = llm._chat_json_copilot_cli("sys", "usr", {"type": "object"}, "gpt-5.4",
                                      cli_path="copilot", timeout_sec=30)
    assert out == {"ok": True}
    assert captured_cmd["cmd"][0] == "copilot"
    assert "--allow-all-tools" in captured_cmd["cmd"]
    assert "--available-tools=" in captured_cmd["cmd"]
    assert "--model" in captured_cmd["cmd"]


def test_chat_json_copilot_cli_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, capture_output, text, encoding, timeout):  # noqa: ANN001
        return SimpleNamespace(returncode=1, stdout="", stderr="Error: bad model")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(llm.LLMError, match="bad model"):
        llm._chat_json_copilot_cli("sys", "usr", {}, "nope", cli_path="copilot", timeout_sec=30)


def test_chat_json_copilot_cli_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, capture_output, text, encoding, timeout):  # noqa: ANN001
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(llm.LLMError, match="timed out"):
        llm._chat_json_copilot_cli("sys", "usr", {}, "m", cli_path="copilot", timeout_sec=1)


def test_chat_json_copilot_cli_gives_actionable_error_on_cmdline_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows, npm's copilot.cmd/.ps1 shim runs through a cmd.exe wrapper
    capped at ~8191 chars; a long prompt raises WinError 206. This should
    surface as a clear, actionable LLMError instead of the raw OSError."""

    def fake_run(cmd, capture_output, text, encoding, timeout):  # noqa: ANN001
        err = OSError("The filename or extension is too long")
        err.winerror = 206
        raise err

    monkeypatch.setattr(llm.os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(llm.LLMError, match="command-line length limit"):
        llm._chat_json_copilot_cli("sys", "usr", {}, "m", cli_path="copilot", timeout_sec=30)


def test_resolve_cli_path_prefers_native_exe_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    llm._resolved_cli_paths.clear()
    monkeypatch.setattr(llm.os, "name", "nt")
    monkeypatch.setattr(llm.shutil, "which", lambda name, **k: r"C:\tools\copilot.exe" if name == "copilot.exe" else None)
    assert llm._resolve_cli_path("copilot") == r"C:\tools\copilot.exe"


def test_resolve_cli_path_falls_back_when_no_exe_found(monkeypatch: pytest.MonkeyPatch) -> None:
    llm._resolved_cli_paths.clear()
    monkeypatch.setattr(llm.os, "name", "nt")
    monkeypatch.setattr(llm.shutil, "which", lambda *a, **k: None)
    assert llm._resolve_cli_path("copilot") == "copilot"


# ---------------------------------------------------------------------------
# anthropic provider: mock the SDK client so no network call is made.
# ---------------------------------------------------------------------------

def test_chat_json_anthropic_returns_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_block = SimpleNamespace(type="tool_use", name="emit_structured_result", input={"x": 1})
    fake_response = SimpleNamespace(content=[fake_block])

    class FakeMessages:
        def create(self, **kwargs):  # noqa: ANN003
            assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_structured_result"}
            return fake_response

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(llm, "_anthropic_sdk_client", lambda api_key, base_url: fake_client)

    out = llm._chat_json_anthropic("sys", "usr", {"type": "object"}, "claude-sonnet-4-5",
                                    api_key="x", base_url="")
    assert out == {"x": 1}


def test_chat_json_anthropic_raises_without_tool_use_block(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = SimpleNamespace(content=[SimpleNamespace(type="text", text="oops")])

    class FakeMessages:
        def create(self, **kwargs):  # noqa: ANN003
            return fake_response

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(llm, "_anthropic_sdk_client", lambda api_key, base_url: fake_client)

    with pytest.raises(llm.LLMError, match="tool_use"):
        llm._chat_json_anthropic("sys", "usr", {}, "claude-sonnet-4-5", api_key="x", base_url="")
