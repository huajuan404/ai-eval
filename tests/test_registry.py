"""U1: runner 注册表加载与校验。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench.registry import (
    RegistryError,
    RunnerProfile,
    get_profile,
    load_registry,
)


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "runners.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_load_four_launcher_types(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          codex:
            launcher: codex
            sandbox: workspace-write
          claude:
            launcher: claude
          glm-5.1:
            launcher: c
            config: 2
          sf-flow:
            launcher: command
            template: "sf run --cwd {cwd} --prompt-file {prompt_file}"
            metrics: none
        """,
    )
    reg = load_registry(p)
    assert set(reg) == {"codex", "claude", "glm-5.1", "sf-flow"}
    assert reg["codex"].launcher == "codex"
    assert reg["codex"].sandbox == "workspace-write"
    assert reg["glm-5.1"].config == "2"
    assert reg["sf-flow"].metrics == "none"
    assert reg["sf-flow"].template.startswith("sf run")


def test_unknown_launcher_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          bad:
            launcher: gemini
        """,
    )
    with pytest.raises(RegistryError, match="launcher='gemini'"):
        load_registry(p)


def test_c_requires_config(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          noconf:
            launcher: c
        """,
    )
    with pytest.raises(RegistryError, match="noconf.*config"):
        load_registry(p)


def test_command_requires_template(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          notmpl:
            launcher: command
        """,
    )
    with pytest.raises(RegistryError, match="notmpl.*template"):
        load_registry(p)


def test_runner_label_rejects_path_segments(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          ../escape:
            launcher: claude
        """,
    )
    with pytest.raises(RegistryError, match="label 非法"):
        load_registry(p)


def test_plaintext_secret_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          leaky:
            launcher: command
            template: "agent --key sk-abcdefghij0123456789 --cwd {cwd}"
        """,
    )
    with pytest.raises(RegistryError, match="明文密钥"):
        load_registry(p)


def test_env_interpolation_allowed(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          ok:
            launcher: command
            template: "agent --cwd {cwd}"
            env:
              MY_TOKEN: "${MY_TOKEN}"
        """,
    )
    reg = load_registry(p)
    assert reg["ok"].env["MY_TOKEN"] == "${MY_TOKEN}"


def test_get_profile_missing_label(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        runners:
          codex:
            launcher: codex
        """,
    )
    reg = load_registry(p)
    with pytest.raises(RegistryError, match=r"未找到 runner 档案 'nope'.*\./run\.sh -l"):
        get_profile(reg, "nope")


def test_with_model_returns_new_object(tmp_path: Path) -> None:
    base = RunnerProfile(label="codex", launcher="codex")
    overridden = base.with_model("gpt-x")
    assert overridden.model == "gpt-x"
    assert base.model is None  # 原档案不变（不可变更新）
    assert overridden is not base


def test_real_registry_loads() -> None:
    """仓库根的 runners.yaml 必须可加载（含四类示例）。"""
    root = Path(__file__).resolve().parent.parent / "runners.yaml"
    reg = load_registry(root)
    assert "codex" in reg and reg["codex"].launcher == "codex"
    assert "claude" in reg and reg["claude"].launcher == "claude"
    assert "glm-5.1" in reg and reg["glm-5.1"].launcher == "c"
    assert reg["minimax-m3-c0-direct"].launcher == "command"
