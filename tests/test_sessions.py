import sys

from opendot import cli
from opendot.agent.config import AgentConfig
from opendot.agent.loop import Agent


def _agent(workdir, *, model="gpt-5.1", system="current system"):
    return Agent(AgentConfig(model=model, workdir=str(workdir), system_prompt=system))


def test_save_load_round_trip_keeps_current_system_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = _agent(workdir, model="ollama/qwen3", system="old system")
    source.messages.extend(
        [
            {"role": "user", "content": "remember this"},
            {"role": "assistant", "content": "remembered"},
        ]
    )

    path = source.save_session()
    resumed = _agent(workdir, model="gpt-5.1", system="new system")

    assert resumed.load_session() is True
    assert path == resumed._session_path()
    assert resumed.messages == [
        {"role": "system", "content": "new system"},
        {"role": "user", "content": "remember this"},
        {"role": "assistant", "content": "remembered"},
    ]
    assert resumed.config.model == "ollama/qwen3"
    assert resumed.reversibility.model == "ollama/qwen3"


def test_sessions_are_keyed_per_project(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = _agent(first_dir)
    second = _agent(second_dir)

    first.messages.append({"role": "user", "content": "first project"})
    first.save_session()

    assert first._session_path() != second._session_path()
    assert second.load_session() is False
    assert second.messages == [{"role": "system", "content": "current system"}]


def test_missing_session_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    workdir = tmp_path / "project"
    workdir.mkdir()
    agent = _agent(workdir)

    assert agent.load_session() is False
    assert agent.messages == [{"role": "system", "content": "current system"}]


def test_corrupt_session_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    workdir = tmp_path / "project"
    workdir.mkdir()
    agent = _agent(workdir)
    agent._session_path().parent.mkdir(parents=True)
    agent._session_path().write_text("not json", encoding="utf-8")

    assert agent.load_session() is False
    assert agent.messages == [{"role": "system", "content": "current system"}]


def test_resume_command_loads_before_starting_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = _agent(workdir, model="ollama/qwen3")
    source.messages.append({"role": "user", "content": "saved context"})
    source.save_session()

    class TtyStdin:
        def isatty(self):
            return True

    started = []
    monkeypatch.setattr(sys, "stdin", TtyStdin())
    monkeypatch.setattr(sys, "argv", ["opendot", "-C", str(workdir), "resume"])
    monkeypatch.setattr("opendot.tui.run_tui", started.append)

    cli.main()

    assert len(started) == 1
    assert started[0].messages[-1] == {"role": "user", "content": "saved context"}
    assert started[0].config.model == "ollama/qwen3"
