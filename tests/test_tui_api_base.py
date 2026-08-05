"""Regression tests for the TUI's local API-base key hint behavior."""

from types import SimpleNamespace

from opendot.tui.app import OpendotTUI


def _hint_app(api_base, *, include_api_base=True):
    config = {"model": "gpt-4o"}
    if include_api_base:
        config["api_base"] = api_base
    app = OpendotTUI.__new__(OpendotTUI)
    app.agent = SimpleNamespace(config=SimpleNamespace(**config))
    app.writes = []
    app._write = lambda *args: app.writes.append(args)
    return app


def test_missing_key_hint_skips_provider_hint_for_local_api_base(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    configured = _hint_app("http://localhost:8080/v1")
    absent = _hint_app(None, include_api_base=False)
    falsey = _hint_app("")

    assert configured._missing_key_hint() is False
    assert configured.writes == []
    assert absent._missing_key_hint() is True
    assert len(absent.writes) == 1
    assert falsey._missing_key_hint() is True
    assert len(falsey.writes) == 1
