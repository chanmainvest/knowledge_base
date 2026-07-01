"""Unit tests for `extract._chunks` (no network/DB access)."""
from __future__ import annotations

from kb import extract


def test_chunks_returns_whole_text_when_under_limit() -> None:
    assert extract._chunks("short text", max_chars=100) == ["short text"]


def test_chunks_splits_on_blank_lines() -> None:
    text = ("a" * 50) + "\n\n" + ("b" * 50) + "\n\n" + ("c" * 50)
    chunks = extract._chunks(text, max_chars=60)
    assert all(len(c) <= 60 for c in chunks)
    assert "".join(chunks).replace("\n\n", "") == "a" * 50 + "b" * 50 + "c" * 50


def test_chunks_never_exceeds_max_chars_for_one_giant_unbroken_paragraph() -> None:
    """Regression test: a transcript/article with no blank-line breaks at
    all (e.g. a raw YouTube transcript) used to produce a single oversized
    chunk far larger than max_chars, which could blow past the OS
    command-line length limit for the `github` provider. See llm.py's
    `_chat_json_copilot_cli` and the WinError 206 handling."""
    # One large paragraph, space-separated "words", no blank lines anywhere.
    text = " ".join(f"word{i}" for i in range(10000))
    assert "\n\n" not in text
    chunks = extract._chunks(text, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    # No content lost: rejoining with spaces reconstructs the original words.
    assert " ".join(chunks).split() == text.split()


def test_chunks_never_exceeds_max_chars_for_unbroken_cjk_text() -> None:
    """CJK text has no spaces between words, so the whitespace-based wrap
    must fall back to a hard character slice."""
    text = "測試" * 20000  # 40,000 chars, no spaces, no blank lines
    chunks = extract._chunks(text, max_chars=5000)
    assert len(chunks) > 1
    assert all(len(c) <= 5000 for c in chunks)
    assert "".join(chunks) == text


def test_wrap_prefers_whitespace_boundaries() -> None:
    words = ["alpha", "beta", "gamma", "delta"]
    text = " ".join(words)
    pieces = extract._wrap(text, max_chars=12)
    assert all(len(p) <= 12 for p in pieces)
    # every word should still appear intact in some piece
    for w in words:
        assert any(w in p for p in pieces)
