"""U7: CLI 解析、config 合并、list、config 加载。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench.__main__ import build_parser, cmd_list, main, merge_config
from bench.config import ConfigError, RunConfig, load_config
from bench.registry import RunnerProfile


def _args(argv):
    return build_parser().parse_args(argv)


def test_runners_override_config() -> None:
    base = RunConfig(runners=("a",), judge="claude", repeat=1)
    merged = merge_config(base, _args(["-r", "codex,glm-5.1"]))
    assert merged.runners == ("codex", "glm-5.1")


def test_case_append_and_repeat_override() -> None:
    base = RunConfig(cases=(), repeat=1)
    merged = merge_config(base, _args(["-c", "seed", "-c", "two", "--repeat", "3"]))
    assert merged.cases == ("seed", "two")
    assert merged.repeat == 3


def test_unspecified_cli_keeps_config() -> None:
    base = RunConfig(runners=("a", "b"), judge="opus-4.7", repeat=2)
    merged = merge_config(base, _args([]))
    assert merged.runners == ("a", "b")
    assert merged.judge == "opus-4.7"
    assert merged.repeat == 2


def test_judge_override() -> None:
    base = RunConfig(judge="claude")
    merged = merge_config(base, _args(["-j", "opus-4.7"]))
    assert merged.judge == "opus-4.7"


def test_cmd_list_shows_runners_and_cases() -> None:
    reg = {
        "codex": RunnerProfile("codex", "codex"),
        "glm-5.1": RunnerProfile("glm-5.1", "c", config="2"),
    }
    out = cmd_list(reg, [])
    assert "codex" in out and "glm-5.1" in out
    assert "可用用例" in out


def test_load_config(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            runners: [codex, claude]
            cases: []
            judge: opus-4.7
            repeat: 3
            dimensions:
              cost: false
            """
        ),
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.runners == ("codex", "claude")
    assert cfg.judge == "opus-4.7"
    assert cfg.repeat == 3
    assert cfg.dimensions["cost"] is False
    assert cfg.dimensions["quality"] is True  # 默认补全


def test_load_config_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="配置文件不存在"):
        load_config(tmp_path / "nope.yaml")


def test_main_list_returns_zero(capsys) -> None:
    rc = main(["-l"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "可用 runner 档案" in captured.out
    assert "codex" in captured.out  # 来自真实 runners.yaml
