"""Regression tests for TUI modal event handling.

The key one: a secret typed into a modal's input (e.g. the Composio API key)
must NEVER bubble up and get submitted as a chat message. This reproduces and
guards the leak where pressing Enter in ApiKeyModal sent the key to the agent.
"""

import pytest

from opendot.tui import OpendotTUI, ApiKeyModal
from opendot.agent.events import Event


class _FakeUsage:
    total_tokens = 0
    cost_usd = 0.0


class _FakeRev:
    def history(self):
        return []


class _FakeConfig:
    model = "gpt-5.1"
    workdir = "/tmp/ws"


class _FakeTB:
    _confirm = None


class _FakeAgent:
    def __init__(self):
        self.usage = _FakeUsage()
        self.reversibility = _FakeRev()
        self.config = _FakeConfig()
        self.toolbox = _FakeTB()
        self.mcp = None
        self.ran = []

    def reset(self):
        pass

    async def run(self, msg):
        self.ran.append(msg)
        yield Event(type="text", text="x")


async def test_apikey_modal_enter_does_not_leak_to_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    app = OpendotTUI(_FakeAgent())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Open the key modal directly (equivalent to running /provider or the
        # first /composio step) and submit a secret with Enter.
        app.push_screen(ApiKeyModal("Composio", "COMPOSIO_API_KEY"))
        await pilot.pause()
        app.screen.query_one("#key").value = "ak_SECRET"
        await pilot.press("enter")
        for _ in range(4):
            await pilot.pause()
        # The secret must not have been submitted to the agent as a message.
        assert "ak_SECRET" not in app.agent.ran


async def test_slash_autocomplete_enter_runs_command(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    app = OpendotTUI(_FakeAgent())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for ch in ["slash", "c", "l", "e", "a", "r"]:
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("enter")  # popup open → runs /clear immediately
        await pilot.pause()
        # /clear ran (no chat turn), input cleared, popup closed.
        assert app.agent.ran == []
        assert app.query_one("#input").value == ""
        assert app.query_one("#cmdpopup").display is False
