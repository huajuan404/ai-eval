"""U3: 用例加载与隔离工作目录。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench.case import (
    CaseError,
    count_changed,
    isolated_workdir,
    load_case,
    snapshot_dir,
)


def _make_case(tmp_path: Path, manifest: str, files: dict[str, str] | None = None) -> Path:
    d = tmp_path / "mycase"
    d.mkdir()
    (d / "case.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def test_load_prompt_case(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: seed
        task:
          type: prompt
          prompt_file: prompts/task.md
        check:
          type: script
          script: check.sh
        judge:
          enabled: true
          rubric_file: prompts/rubric.md
          dimensions: [correctness, quality]
        """,
        {"prompts/task.md": "Implement add()", "prompts/rubric.md": "Score 0-10"},
    )
    case = load_case(d)
    assert case.name == "seed"
    assert case.task.type == "prompt"
    assert case.task.prompt == "Implement add()"
    assert case.check.type == "script" and case.check.script == "check.sh"
    assert case.judge.enabled and case.judge.rubric == "Score 0-10"
    assert case.judge.dimensions == ("correctness", "quality")


def test_load_skill_case_renders_invocation(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: skillcase
        task:
          type: skill
          skill: my-skill
          args: --flag x
        requires_engine: claude
        """,
    )
    case = load_case(d)
    assert case.task.type == "skill"
    assert case.task.prompt == "/my-skill --flag x"
    assert case.requires_engine == "claude"
    assert case.supports_launcher("claude") is True
    assert case.supports_launcher("codex") is False


def test_prompt_case_missing_file_errors(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: bad
        task:
          type: prompt
          prompt_file: prompts/missing.md
        """,
    )
    with pytest.raises(CaseError, match="prompt_file 不存在"):
        load_case(d)


def test_invalid_task_type_errors(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: bad
        task:
          type: telepathy
        """,
    )
    with pytest.raises(CaseError, match="task.type='telepathy'"):
        load_case(d)


def test_supports_launcher_default_all(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: anyengine
        task:
          type: prompt
          prompt_file: p.md
        """,
        {"p.md": "x"},
    )


# ── class / expected 字段 ─────────────────────────


def test_class_field_validated(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: ok
        class: tool-using
        task:
          type: prompt
          prompt_file: p.md
        """,
        {"p.md": "x"},
    )
    case = load_case(d)
    assert case.class_ == "tool-using"


def test_class_field_invalid_errors(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: bad
        class: nonsense
        task:
          type: prompt
          prompt_file: p.md
        """,
        {"p.md": "x"},
    )
    with pytest.raises(CaseError, match="class='nonsense'"):
        load_case(d)


def test_class_field_defaults_to_coding(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: noclass
        task:
          type: prompt
          prompt_file: p.md
        """,
        {"p.md": "x"},
    )
    assert load_case(d).class_ == "coding"


def test_expected_field_loaded_as_dict(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: exp
        class: tool-using
        task:
          type: prompt
          prompt_file: p.md
        expected:
          commit: abc1234
          algo: MD5
        """,
        {"p.md": "x"},
    )
    case = load_case(d)
    assert case.expected == {"commit": "abc1234", "algo": "MD5"}


def test_expected_field_invalid_errors(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: bad
        task:
          type: prompt
          prompt_file: p.md
        expected: "not a dict"
        """,
        {"p.md": "x"},
    )
    with pytest.raises(CaseError, match=r"expected"):
        load_case(d)


def test_isolated_workdir_copies_input_and_preserves_source(tmp_path: Path) -> None:
    d = _make_case(
        tmp_path,
        """
        name: c
        task:
          type: prompt
          prompt_file: p.md
        """,
        {"p.md": "x", "input/data.txt": "original"},
    )
    case = load_case(d)
    captured = None
    with isolated_workdir(case) as wd:
        captured = wd
        assert (wd / "data.txt").read_text() == "original"
        # 在 workdir 改文件，不应影响源
        (wd / "data.txt").write_text("mutated")
        (wd / "new.py").write_text("print(1)")
    # 退出后临时目录清理
    assert not captured.exists()
    # 源 input 未被污染
    assert (case.input_dir / "data.txt").read_text() == "original"


def test_snapshot_diff_counts_and_ignores_noise(tmp_path: Path) -> None:
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "a.txt").write_text("1")
    before = snapshot_dir(wd)
    # 真实改动：改 a.txt、新增 b.txt
    (wd / "a.txt").write_text("12")
    (wd / "b.txt").write_text("new")
    # 噪声：launcher 自建 .omx/ 与 __pycache__
    (wd / ".omx").mkdir()
    (wd / ".omx" / "sess.json").write_text("{}")
    (wd / "__pycache__").mkdir()
    (wd / "__pycache__" / "x.pyc").write_text("bin")
    after = snapshot_dir(wd)
    assert count_changed(before, after) == 2  # a 修改 + b 新增；噪声不计
