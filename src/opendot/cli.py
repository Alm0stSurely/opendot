"""opendot CLI — the interactive chat REPL (default) plus a one-shot mode.

    opendot                      # open an interactive chat session
    opendot -p "list my files"   # run one task and exit (scripting/CI)
    opendot --model ollama/qwen2.5

This is a thin layer over the core Agent. The richer TUI (panels/live boxes) is
a later milestone; v1 uses a clean Rich-rendered REPL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from opendot import __version__
from opendot.agent.config import AgentConfig
from opendot.agent.loop import Agent
from opendot.agent.prompt import DEFAULT_SYSTEM_PROMPT

console = Console()

SLASH_HELP = """\
[bold]Commands[/bold]
  /help     show this help
  /log      show the auditable history of actions taken
  /undo     revert the last action ( /undo <id> to restore to a point )
  /clear    reset the conversation
  /compact  trim old conversation turns to free up context
  /model    show the current model ( /model <id> to switch )
  /provider connect a provider ( /provider <ENV_VAR> <api-key> )
  exit      quit (also: /exit, /quit, Ctrl-D)
"""


def _load_project_context(workdir: str) -> str | None:
    """Append an OPENDOT.md from the working dir to the system prompt, if present."""
    p = Path(workdir) / "OPENDOT.md"
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
    return None


def _confirm(prompt: str) -> bool:
    """Ask the user to approve an irreversible action (interactive only)."""
    console.print(f"\n[bold yellow]⚠ {prompt}[/bold yellow]")
    try:
        ans = input("  [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"y", "yes"}


def _warn_if_missing_key(model: str) -> None:
    """Print a friendly hint if the model's expected API key isn't in the env.

    A warning only — we still let the call proceed, because LiteLLM may find the
    key another way and we'd rather not falsely block a working setup.
    """
    from opendot.providers import env_var_for

    env_var = env_var_for(model)
    if env_var and not os.environ.get(env_var):
        console.print(
            f"[yellow]![/yellow] No [bold]{env_var}[/bold] found in your environment "
            f"for model [cyan]{model}[/cyan].\n"
            f"  Set it (e.g. [dim]export {env_var}=...[/dim]), or pick another model "
            f"with [dim]--model[/dim] / [dim]$OPENDOT_MODEL[/dim].\n"
            f"  See the model table: https://github.com/vedaant00/opendot#any-model\n"
        )


def _build_agent(model: str, workdir: str, confirm=None, api_base: str | None = None) -> Agent:
    # With a custom api_base (local OpenAI-compatible server like llama.cpp), no
    # provider key is needed — so skip the auto-switch. Otherwise: if the chosen
    # model's key isn't set but another provider's is, switch to that provider.
    if not api_base:
        from opendot.providers import env_var_for, model_for_available_key

        var = env_var_for(model)
        if var and not os.environ.get(var):
            alt = model_for_available_key()
            if alt and alt != model:
                console.print(
                    f"[dim]no {var}; using [cyan]{alt}[/cyan] "
                    f"(found its key in your environment)[/dim]"
                )
                model = alt

    system = DEFAULT_SYSTEM_PROMPT
    ctx = _load_project_context(workdir)
    if ctx:
        system += "\n\n# Project context (OPENDOT.md)\n" + ctx

    # Connect to any configured MCP servers (~/.opendot/mcp.json).
    mcp_manager = None
    try:
        from opendot.mcp import MCPManager, load_mcp_config

        cfg = load_mcp_config()
        if cfg:
            mcp_manager = MCPManager(cfg)
            mcp_manager.start()
    except Exception:  # noqa: BLE001 - MCP is optional; never block startup
        mcp_manager = None

    return Agent(
        AgentConfig(model=model, workdir=workdir, system_prompt=system, api_base=api_base),
        confirm=confirm,
        mcp_manager=mcp_manager,
    )


def _cmd_log(workdir: str, clear: bool = False) -> None:
    """`opendot log` — show the auditable action history (or --clear it)."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    rev = Reversibility(workdir=workdir, rules=load_rules(workdir))
    if clear:
        n = rev.clear_history()
        console.print(
            f"[green]cleared[/green] {n} ledger entr{'y' if n == 1 else 'ies'} "
            "(undo history discarded)"
        )
        return
    entries = rev.history()
    if not entries:
        console.print("[dim]no actions recorded yet[/dim]")
        return
    console.print("[bold]opendot action history[/bold] (most recent last)\n")
    for e in entries:
        mark = "[green]↺[/green]" if e.reversible else "[red]✗ irreversible[/red]"
        detail = e.detail if len(e.detail) < 70 else e.detail[:67] + "..."
        console.print(f"  {e.id}  {mark}  [cyan]{e.kind}[/cyan]  {detail}")
        if e.note:
            console.print(f"        [dim]{e.note}[/dim]")
        if e.model:
            params = "".join(f" {k}={v}" for k, v in e.params.items())
            console.print(f"        [dim]model: {e.model}{params}[/dim]")
    console.print("\n[dim]opendot undo           revert the last action[/dim]")
    console.print("[dim]opendot undo <id>      restore the workspace to before that action[/dim]")


def _cmd_undo(workdir: str, snap_id: str | None) -> None:
    """`opendot undo [id]` — restore the workspace."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    rev = Reversibility(workdir=workdir, rules=load_rules(workdir))
    entries = rev.history()
    if not entries:
        console.print("[dim]nothing to undo[/dim]")
        return
    if snap_id:
        target = next((e for e in entries if e.id == snap_id), None)
        if target is None:
            console.print(f"[red]no action with id {snap_id}[/red]  (see: opendot log)")
            return
        changed_locks = rev.restore_to(target.snapshot_before)
        console.print(
            f"[green]restored[/green] workspace to before action {snap_id} ({target.kind}: {target.detail[:50]})"
        )
        _note_lockfiles(console, changed_locks)
    else:
        undone = rev.undo_last()
        if undone is None:
            console.print(
                "[yellow]nothing to undo[/yellow] (or the last action wasn't snapshotted)"
            )
            return
        console.print(f"[green]undid[/green] last action ({undone.kind}: {undone.detail[:50]})")
        _note_lockfiles(console, rev.last_changed_lockfiles)


def _note_lockfiles(console, changed: list[str]) -> None:
    """`opendot undo` is a non-interactive command with no model in the loop, so
    it states the fact plainly: a lockfile was rolled back, meaning the installed
    environment no longer matches until the package manager is re-run. (In the
    interactive TUI the agent narrates this in its own words instead.)"""
    if not changed:
        return
    names = ", ".join(changed)
    console.print(
        f"[bold yellow]note:[/bold yellow] lockfile(s) rolled back ([bold]{names}[/bold]) — "
        f"installed packages are unchanged; re-run your package manager to match."
    )


def _cmd_mcp(args) -> None:
    """`opendot mcp add|list|remove|test` — manage external MCP servers."""
    from opendot.mcp import (
        MCPManager,
        add_mcp_server,
        load_mcp_config,
        remove_mcp_server,
    )

    cmd = getattr(args, "mcp_command", None)

    if cmd == "list" or cmd is None:
        servers = load_mcp_config()
        if not servers:
            console.print("[dim]no MCP servers configured.[/dim]")
            console.print(
                "[dim]add one:  opendot mcp add <name> -- <command> [args...]   (or --url <url>)[/dim]"
            )
            return
        console.print("[bold]MCP servers[/bold] (from ~/.opendot/mcp.json)\n")
        for name, spec in servers.items():
            if spec.get("url"):
                target = spec["url"]
            else:
                target = " ".join([spec.get("command", "")] + spec.get("args", []))
            console.print(f"  [cyan]{name}[/cyan]  {target}")
        return

    if cmd == "add":
        env = {}
        for pair in args.env:
            if "=" in pair:
                k, v = pair.split("=", 1)
                env[k] = v
        if args.url:
            spec = {"url": args.url}
            if getattr(args, "oauth", False):
                spec["auth"] = "oauth"  # browser-OAuth; opendot obtains tokens itself
            headers = {}
            for pair in getattr(args, "header", []):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    headers[k.strip()] = v.strip()
            if headers and "auth" not in spec:
                spec["headers"] = headers  # e.g. Authorization=Bearer <token>
        else:
            cmd = list(getattr(args, "post_dashdash", []))
            if not cmd:
                console.print(
                    "[red]provide a launch command after `--`, or use --url for a remote server[/red]"
                )
                return
            spec = {"command": cmd[0]}
            if len(cmd) > 1:
                spec["args"] = cmd[1:]
        if env:
            spec["env"] = env
        add_mcp_server(args.name, spec)
        if spec.get("auth") == "oauth":
            from opendot.mcp import authorize_oauth_server

            console.print(f"opening your browser to authorize [cyan]{args.name}[/cyan]…")
            res = authorize_oauth_server(args.name, spec)
            if res.ok:
                console.print(
                    f"[green]✓ authorized[/green] [cyan]{args.name}[/cyan] — "
                    f"{res.tool_count} tools. They load next time you run opendot."
                )
            else:
                console.print(
                    f"[red]✗ authorization failed[/red] — {res.error}. "
                    f"The server is saved; retry with `opendot mcp test {args.name}`."
                )
            return
        console.print(
            f"[green]added[/green] MCP server [cyan]{args.name}[/cyan]. It will connect next time you run opendot."
        )
        return

    if cmd == "test":
        servers = load_mcp_config()
        spec = servers.get(args.name)
        if spec is None:
            console.print(f"[red]no MCP server named {args.name!r}.[/red]")
            return
        manager = MCPManager({args.name: spec})
        try:
            manager.start()
            if args.name in manager.connected:
                names = [tool.name for tool in manager.tools if tool.server == args.name]
                suffix = f": {', '.join(names)}" if names else ""
                tool_label = "tool" if len(names) == 1 else "tools"
                console.print(f"[green]✓ connected[/green] — {len(names)} {tool_label}{suffix}")
            else:
                error = manager.errors.get(args.name, "connection did not complete")
                console.print(f"[red]✗ connection failed[/red] — {error}")
        finally:
            manager.shutdown()
        return

    if cmd == "remove":
        if remove_mcp_server(args.name):
            console.print(f"[green]removed[/green] MCP server [cyan]{args.name}[/cyan].")
        else:
            console.print(f"[dim]no MCP server named {args.name!r}.[/dim]")
        return


async def _run_turn(agent: Agent, message: str, *, rich: bool = True) -> None:
    """Run one turn, streaming events to the console live.

    Reasoning ("thinking") streams dimmed; the answer streams in normal weight;
    tool activity prints as it happens. Text is streamed token-by-token.
    """
    mode = None  # track what we're currently printing: "thinking" | "text" | None

    def _switch(new: str, label: str = "") -> None:
        nonlocal mode
        if mode != new:
            if mode is not None:
                console.print()  # newline between phases
            if label:
                console.print(label)
            mode = new

    async for ev in agent.run(message):
        if ev.type == "thinking":
            _switch("thinking", "[dim italic]thinking…[/dim italic]")
            console.print(f"[dim]{ev.text}[/dim]", end="", markup=False, soft_wrap=True)
        elif ev.type == "text":
            _switch("text")
            console.print(ev.text, end="", markup=False, soft_wrap=True)
        elif ev.type == "tool_start":
            _switch("tool")
            mode = None  # tool output isn't a text phase
            arg_preview = ", ".join(f"{k}={v!r}"[:60] for k, v in ev.args.items())
            console.print(f"[cyan]▸[/cyan] [bold]{ev.tool}[/bold][dim]({arg_preview})[/dim]")
        elif ev.type == "tool_end":
            first = (ev.result.strip().splitlines() or ["(done)"])[0]
            console.print(f"  [dim]{first[:100]}[/dim]")
        elif ev.type == "final":
            if mode is not None:
                console.print()
        elif ev.type == "error":
            if mode is not None:
                console.print()
            console.print(f"[bold red]error:[/bold red] {ev.text}")
            mode = None


def _interactive(agent: Agent) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    console.print(
        Panel.fit(
            f"[bold]opendot[/bold] v{__version__}  ·  model: [cyan]{agent.config.model}[/cyan]\n"
            f"working in [dim]{agent.config.workdir}[/dim]\n"
            "Type a message. /help for commands, exit to quit.",
            border_style="cyan",
        )
    )
    session: PromptSession = PromptSession(history=InMemoryHistory())

    while True:
        try:
            text = session.prompt("\nopendot › ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return

        if not text:
            continue
        low = text.lower()
        if low in {"exit", "/exit", "/quit", "quit"}:
            console.print("[dim]bye[/dim]")
            return
        if low == "/help":
            console.print(SLASH_HELP)
            continue
        if low == "/clear":
            agent.reset()
            console.print("[dim]context cleared[/dim]")
            continue
        if low == "/compact":
            dropped = agent.compact()
            console.print(f"[dim]compacted: dropped {dropped} old message(s)[/dim]")
            continue
        if low.startswith("/model"):
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                agent.config.model = parts[1].strip()
                console.print(f"model → [cyan]{agent.config.model}[/cyan]")
                _warn_if_missing_key(agent.config.model)
            else:
                console.print(
                    f"model: [cyan]{agent.config.model}[/cyan]  ([dim]/model <id> to change[/dim])"
                )
            continue
        if low.startswith("/provider") or low.startswith("/connect"):
            from opendot.providers import register_key

            parts = text.split()
            if len(parts) == 3:
                register_key(parts[1], parts[2])
                console.print(
                    f"[green]✓[/green] set {parts[1]} for this session — "
                    f"persist with [dim]export {parts[1]}=…[/dim]"
                )
            else:
                console.print(
                    "usage: [dim]/provider <ENV_VAR> <api-key>[/dim]  "
                    "(the TUI has an interactive picker)"
                )
            continue
        if low == "/log":
            _cmd_log(agent.config.workdir)
            continue
        if low.startswith("/undo"):
            parts = text.split(maxsplit=1)
            _cmd_undo(agent.config.workdir, parts[1].strip() if len(parts) > 1 else None)
            continue
        if low.startswith("/"):
            console.print(f"[dim]unknown command {text!r} — /help for the list[/dim]")
            continue

        try:
            asyncio.run(_run_turn(agent, text))
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")


def main() -> None:
    # Split off a launch command after `--` (for `opendot mcp add NAME -- cmd ...`)
    # before argparse, so the command's own flags aren't parsed by opendot.
    post_dashdash: list[str] = []
    argv = sys.argv[1:]
    if "--" in argv:
        i = argv.index("--")
        argv, post_dashdash = argv[:i], argv[i + 1 :]

    parser = argparse.ArgumentParser(
        prog="opendot",
        description="An interactive terminal AI agent you can fully undo.",
    )
    parser.add_argument("-p", "--prompt", help="Run a single task and exit (one-shot mode).")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENDOT_MODEL", "gpt-5.1"),
        help="Model id (any LiteLLM model, e.g. gpt-5.1, claude-opus-4-5, ollama/qwen3).",
    )
    parser.add_argument(
        "--api-base",
        default=os.environ.get("OPENAI_API_BASE"),
        help="Base URL of an OpenAI-compatible server "
        "(llama.cpp/llama-server, vLLM, LM Studio). "
        "Use with --model openai/<name>.",
    )
    parser.add_argument(
        "-C", "--dir", default=os.getcwd(), help="Working directory (default: cwd)."
    )
    parser.add_argument(
        "--repl",
        action="store_true",
        help="Use the plain REPL instead of the full-screen TUI.",
    )
    parser.add_argument("--version", action="version", version=f"opendot {__version__}")

    sub = parser.add_subparsers(dest="command")
    p_log = sub.add_parser("log", help="Show the auditable history of actions opendot took.")
    p_log.add_argument(
        "--clear",
        action="store_true",
        help="Wipe this project's action ledger (discards undo history).",
    )
    p_undo = sub.add_parser("undo", help="Restore the workspace (last action, or to a given id).")
    p_undo.add_argument("id", nargs="?", help="Action id from `opendot log` (default: last).")

    # opendot mcp add/list/remove
    p_mcp = sub.add_parser("mcp", help="Manage MCP servers opendot connects to.")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_command")
    p_add = mcp_sub.add_parser(
        "add",
        help="Add an MCP server.",
        epilog="For a stdio server, put its launch command after `--`. "
        "For a remote server, pass --url instead.",
    )
    p_add.add_argument("name", help="A short name for the server.")
    p_add.add_argument("--url", help="A remote MCP server URL (http/sse) instead of a command.")
    p_add.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Environment variable for the server (repeatable).",
    )
    p_add.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="HTTP header for a remote (--url) server, e.g. "
        "'Authorization=Bearer <token>' (repeatable).",
    )
    p_add.add_argument(
        "--oauth",
        action="store_true",
        help="Authorize a remote (--url) server via browser OAuth instead of a static "
        "token. Opens your browser to authorize when you add it.",
    )
    # The launch command (after `--`) is captured from argv in main(), not here,
    # so its own flags (e.g. -y) aren't parsed by argparse.
    mcp_sub.add_parser("list", help="List configured MCP servers.")
    p_rm = mcp_sub.add_parser("remove", help="Remove an MCP server.")
    p_rm.add_argument("name", help="Server name to remove.")
    p_test = mcp_sub.add_parser("test", help="Connect to one server and list its tools.")
    p_test.add_argument("name", help="Configured server name to test.")

    args = parser.parse_args(argv)
    args.post_dashdash = post_dashdash
    workdir = os.path.abspath(args.dir)

    # Reversibility subcommands (no model call needed).
    if args.command == "log":
        _cmd_log(workdir, clear=getattr(args, "clear", False))
        return
    if args.command == "undo":
        _cmd_undo(workdir, args.id)
        return
    if args.command == "mcp":
        _cmd_mcp(args)
        return

    # Everything below this point calls a model — hint if the key looks missing.
    if not args.api_base:
        _warn_if_missing_key(args.model)

    # One-shot: -p flag, or piped stdin.
    oneshot = args.prompt
    if oneshot is None and not sys.stdin.isatty():
        oneshot = sys.stdin.read().strip() or None

    if oneshot:
        # Non-interactive: can't prompt, so decline irreversible commands by default.
        agent = _build_agent(args.model, workdir, confirm=lambda _p: False, api_base=args.api_base)
        asyncio.run(_run_turn(agent, oneshot, rich=False))
    elif args.repl:
        agent = _build_agent(args.model, workdir, confirm=_confirm, api_base=args.api_base)
        _interactive(agent)
    else:
        # Default: the full-screen TUI. It installs its own confirm callback
        # (a blocking modal) on the agent's toolbox, so irreversible commands are
        # confirmed in-app. The placeholder here is replaced in OpendotTUI.__init__.
        from opendot.tui import run_tui

        agent = _build_agent(args.model, workdir, confirm=lambda _p: False, api_base=args.api_base)
        run_tui(agent)


if __name__ == "__main__":
    main()
