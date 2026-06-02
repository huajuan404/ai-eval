"""U1: 适配器接口共享工具 + run record schema。"""

from __future__ import annotations

from bench.adapters.base import (
    ParsedOutput,
    build_minimal_env,
    extract_json_object,
    normalize_model_label,
)
from bench.record import (
    Agentic,
    CheckResult,
    JudgeResult,
    RunRecord,
    Usage,
)
from bench.registry import RunnerProfile


def test_extract_json_skips_banner_prefix() -> None:
    stdout = '🚀 启动 Claude Code\nAPI: https://x\n{"result": "ok", "n": 1}\n'
    obj = extract_json_object(stdout)
    assert obj == {"result": "ok", "n": 1}


def test_extract_json_handles_nested_and_strings() -> None:
    stdout = 'noise {"a": {"b": 1}, "s": "has } brace"} trailing'
    obj = extract_json_object(stdout)
    assert obj == {"a": {"b": 1}, "s": "has } brace"}


def test_extract_json_returns_none_on_garbage() -> None:
    assert extract_json_object("no json here") is None


def test_normalize_model_label_strips_bracket_suffix() -> None:
    assert normalize_model_label("deepseek-v4-pro[1m]") == "deepseek-v4-pro"
    assert normalize_model_label("deepseek-v4-pro[1m][1m]") == "deepseek-v4-pro"
    assert normalize_model_label("  glm-5.1 ") == "glm-5.1"


def test_minimal_env_excludes_credentials() -> None:
    profile = RunnerProfile(label="x", launcher="codex")
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "ANTHROPIC_API_KEY": "sk-secret",
        "OPENAI_TOKEN": "tok",
        "RANDOM_SECRET": "s",
    }
    env = build_minimal_env(profile, base)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/u"
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_TOKEN" not in env
    assert "RANDOM_SECRET" not in env


def test_minimal_env_resolves_profile_interpolation() -> None:
    profile = RunnerProfile(
        label="x", launcher="command", template="t", env={"MY_TOKEN": "${SRC_TOKEN}"}
    )
    base = {"PATH": "/usr/bin", "SRC_TOKEN": "abc123"}
    env = build_minimal_env(profile, base)
    assert env["MY_TOKEN"] == "abc123"


def test_parsed_output_defaults() -> None:
    po = ParsedOutput()
    assert po.usage is None
    assert po.num_turns is None
    assert po.is_error is False


def test_usage_from_tokens_computes_total() -> None:
    u = Usage.from_tokens(100, 50, cost_usd=0.01)
    assert u.total_tokens == 150
    assert u.cost_usd == 0.01


def test_usage_from_tokens_all_none() -> None:
    u = Usage.from_tokens(None, None)
    assert u.total_tokens is None
    assert u.cost_usd is None


def test_run_record_json_roundtrip_with_nulls() -> None:
    rec = RunRecord(
        case="seed",
        runner_label="codex",
        launcher_type="codex",
        runner_model="gpt-x",
        repeat_index=1,
        duration_ms=1234,
        exit_code=0,
        usage=None,  # 降级
        agentic=Agentic(num_turns=3, files_changed=2),
        check=CheckResult(ran=True, passed=True, detail="ok"),
        judge=None,
    )
    restored = RunRecord.from_json(rec.to_json())
    assert restored == rec
    assert restored.usage is None
    assert restored.agentic.files_changed == 2


def test_run_record_json_roundtrip_full() -> None:
    rec = RunRecord(
        case="seed",
        runner_label="claude",
        launcher_type="claude",
        usage=Usage.from_tokens(10, 20, 0.001),
        judge=JudgeResult(ran=True, model="claude", score=8, max=10, dimensions={"q": 8}),
    )
    restored = RunRecord.from_json(rec.to_json())
    assert restored == rec
    assert restored.usage.total_tokens == 30
    assert restored.judge.dimensions == {"q": 8}
