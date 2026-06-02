"""U2: 四类启动器适配器的命令构造、最小环境与 usage 解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.adapters import get_adapter
from bench.adapters.c import CAdapter
from bench.adapters.claude import ClaudeAdapter
from bench.adapters.codex import CodexAdapter
from bench.adapters.command import CommandAdapter
from bench.registry import RunnerProfile

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ── claude ───────────────────────────────────────────
def test_claude_parses_usage_from_fixture() -> None:
    stdout = (FIXTURES / "claude_result.json").read_text(encoding="utf-8")
    out = ClaudeAdapter().parse(stdout, "", 0)
    assert out.usage.input_tokens == 42000
    assert out.usage.output_tokens == 850
    assert out.usage.total_tokens == 42850
    assert out.usage.cost_usd == 0.0123
    assert out.num_turns == 4
    assert out.is_error is False


def test_claude_is_error_flag() -> None:
    stdout = '{"type":"result","is_error":true,"num_turns":1,"result":"boom","usage":{"input_tokens":5,"output_tokens":1}}'
    out = ClaudeAdapter().parse(stdout, "", 0)
    assert out.is_error is True


def test_claude_build_command_includes_model_and_headless() -> None:
    p = RunnerProfile(label="claude", launcher="claude", model="opus")
    cmd = ClaudeAdapter().build_command(p, "do it", "/tmp/wd")
    assert cmd[:5] == ["claude", "-p", "do it", "--output-format", "json"]
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[-2:] == ["--model", "opus"]


# ── codex ────────────────────────────────────────────
def test_codex_sums_turn_usage() -> None:
    stdout = (FIXTURES / "codex_events.jsonl").read_text(encoding="utf-8")
    out = CodexAdapter().parse(stdout, "", 0)
    assert out.usage.input_tokens == 1000 + 1500 + 800  # 3300
    assert out.usage.output_tokens == 200 + 300 + 120  # 620
    assert out.usage.cost_usd is None  # codex 无成本字段
    assert out.num_turns == 3


def test_codex_no_usage_events_degrades() -> None:
    stdout = '{"type":"session.created","session_id":"x"}\n{"type":"item.completed"}\n'
    out = CodexAdapter().parse(stdout, "", 0)
    assert out.usage is None
    assert out.num_turns is None


def test_codex_build_command_defaults_workspace_write() -> None:
    p = RunnerProfile(label="codex", launcher="codex")
    cmd = CodexAdapter().build_command(p, "fix bug", "/tmp/wd")
    assert cmd[:3] == ["codex", "exec", "fix bug"]
    assert "--json" in cmd
    assert cmd[cmd.index("-C") + 1] == "/tmp/wd"
    assert cmd[cmd.index("-s") + 1] == "workspace-write"


def test_codex_error_event_sets_is_error() -> None:
    stdout = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n{"type":"stream.error","message":"x"}\n'
    out = CodexAdapter().parse(stdout, "", 0)
    assert out.is_error is True


# ── c ────────────────────────────────────────────────
def test_c_build_command_passthrough() -> None:
    p = RunnerProfile(label="glm-5.1", launcher="c", config="2")
    cmd = CAdapter().build_command(p, "task", "/tmp/wd")
    assert cmd == ["c", "2", "-p", "task", "--output-format", "json"]


def test_c_parses_through_banner() -> None:
    stdout = (FIXTURES / "c_with_banner.txt").read_text(encoding="utf-8")
    out = CAdapter().parse(stdout, "", 0)
    assert out.usage.input_tokens == 300
    assert out.usage.output_tokens == 100
    assert out.num_turns == 2


# ── command ──────────────────────────────────────────
def test_command_renders_placeholders() -> None:
    p = RunnerProfile(
        label="sf",
        launcher="command",
        template="agent --cwd {cwd} --prompt-file {prompt_file} --model {model}",
        model="x",
    )
    cmd = CommandAdapter().build_command(p, "hello", "/tmp/wd")
    assert cmd[0] == "/bin/sh" and cmd[1] == "-c"
    rendered = cmd[2]
    assert "/tmp/wd" in rendered
    assert "/tmp/wd/PROMPT.txt" in rendered
    assert "--model x" in rendered


def test_command_unknown_placeholder_errors() -> None:
    p = RunnerProfile(
        label="bad", launcher="command", template="agent --skill {skill}"
    )
    with pytest.raises(ValueError, match="未知占位符"):
        CommandAdapter().build_command(p, "x", "/tmp/wd")


def test_command_metrics_none_no_usage() -> None:
    adapter = CommandAdapter()
    assert adapter.supports_usage is False
    out = adapter.parse("whatever output", "", 0)
    assert out.usage is None


# ── 环境最小化 ───────────────────────────────────────
def test_codex_env_strips_unrelated_credentials() -> None:
    p = RunnerProfile(label="codex", launcher="codex")
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "OPENAI_API_KEY": "sk-own",
        "ANTHROPIC_API_KEY": "sk-other",
        "GITHUB_TOKEN": "ght",
    }
    env = CodexAdapter().build_env(p, base)
    assert env["OPENAI_API_KEY"] == "sk-own"  # 自身凭证放行
    assert "ANTHROPIC_API_KEY" not in env  # 其它 provider 凭证剔除
    assert "GITHUB_TOKEN" not in env


def test_get_adapter_dispatch() -> None:
    assert isinstance(get_adapter(RunnerProfile("a", "claude")), ClaudeAdapter)
    assert isinstance(get_adapter(RunnerProfile("b", "codex")), CodexAdapter)
    assert isinstance(get_adapter(RunnerProfile("c", "c", config="0")), CAdapter)
    assert isinstance(
        get_adapter(RunnerProfile("d", "command", template="x")), CommandAdapter
    )
