"""Jev's typed response contract and aggregation without network access."""
from __future__ import annotations

import ssl
from types import SimpleNamespace

import pytest

from kb import jev


def test_classify_aggregates_chunk_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev, "settings", lambda: SimpleNamespace(
        jev_api_key="test-key", jev_model="jev-latest",
        jev_base_url="https://api.typesafe.ai"))
    monkeypatch.setattr("kb.extract._chunks", lambda text: ["first", "second"])
    calls = []

    class Response:
        def __init__(self, index):
            self.index = index

        def raise_for_status(self):
            pass

        def json(self):
            values = {name: 0.1 for name in jev._QUESTIONS}
            if self.index == 0:
                values["mentions_book"] = 0.9
                values["is_marketing"] = 0.9
            else:
                values["stock_hong_kong"] = 0.8
            return {"answers": {name: {"type": "noul", "noul": value}
                                for name, value in values.items()},
                    "usage": {"input_tokens": 12, "output_tokens": 3}}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert isinstance(kwargs["verify"], ssl.SSLContext)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, headers, json):
            assert headers["Authorization"] == "Bearer test-key"
            assert json["model"] == "jev-latest"
            assert set(json["questions"]) == set(jev._QUESTIONS)
            calls.append(json["state"])
            return Response(len(calls) - 1)

    monkeypatch.setattr(jev.httpx, "Client", Client)
    result, usage = jev.classify("sample")
    assert calls == ["first", "second"]
    assert result["decisions"]["mentions_book"] is True
    assert result["decisions"]["stock_hong_kong"] is True
    assert result["decisions"]["is_marketing"] is False
    assert result["probabilities"]["is_marketing"] == 0.5
    assert usage == {"input_tokens": 24, "output_tokens": 6}


def test_tls_context_ignores_ssl_keylogfile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSLKEYLOGFILE", "unavailable-keylog-path")
    context = jev._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
