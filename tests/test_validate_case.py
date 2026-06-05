"""U6：落盘校验门 — 真实 case 集成 + 路径信任 + 草稿 TODO。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import validate_case as vc
from session_extract import ConfigError

REPO = Path(__file__).resolve().parent.parent
CASES = REPO / "cases"


def test_validate_real_fizzbuzz_complete():
    res = vc.validate_case(CASES / "2026-06-02-001-fizzbuzz", REPO)
    assert res.valid is True, res.errors
    assert res.complete is True, res.todos  # coding 有 verify/ 测试


def test_validate_real_planning_complete():
    res = vc.validate_case(CASES / "2026-06-03-003-planning", REPO)
    assert res.valid is True, res.errors
    assert res.complete is True, res.todos  # reasoning，rubric + expected 阈值


def test_validate_real_git_bisect_complete():
    res = vc.validate_case(CASES / "2026-06-03-001-git-bisect-bug-hunt", REPO)
    assert res.valid is True, res.errors
    # tool-using：expected.commit 是真值 → complete
    assert res.complete is True, res.todos


def test_path_trust_rejects_non_bench_dir(tmp_path):
    with pytest.raises(ConfigError):
        vc.validate_ai_eval_path(tmp_path)


def test_path_trust_accepts_repo():
    assert vc.validate_ai_eval_path(REPO) == REPO.resolve()


def _write_case(d: Path, *, cls: str, expected: str = "", extra_check: bool = True):
    d.mkdir(parents=True)
    (d / "prompts").mkdir()
    (d / "prompts" / "task.md").write_text("做这个任务，输出 ANSWER: <x>", encoding="utf-8")
    (d / "prompts" / "rubric.md").write_text(
        '按维度评分。返回 JSON {"score":n,"max":n,"dimensions":{},"reasoning":"x"}', encoding="utf-8"
    )
    check_block = "check: {type: script, script: check.sh}" if extra_check else "check: {type: none}"
    if extra_check:
        (d / "check.sh").write_text("#!/bin/bash\ngrep ANSWER OUTPUT.txt", encoding="utf-8")
    (d / "case.yaml").write_text(
        textwrap.dedent(f"""\
        name: t
        class: {cls}
        task: {{type: prompt, prompt_file: prompts/task.md}}
        {check_block}
        judge: {{enabled: true, rubric_file: prompts/rubric.md, dimensions: [accuracy]}}
        {expected}
        """),
        encoding="utf-8",
    )


def test_tool_using_todo_when_ground_truth_is_stub(tmp_path):
    d = tmp_path / "2026-06-05-001-draft"
    _write_case(d, cls="tool-using", expected='expected: {answer: "<TODO 外部真值>"}')
    res = vc.validate_case(d, REPO)
    assert res.valid is True  # 结构合法、能加载
    assert res.complete is False  # 但真值是桩 → 未完成
    assert any("ground-truth" in t for t in res.todos)


def test_tool_using_complete_with_real_answer(tmp_path):
    d = tmp_path / "2026-06-05-002-real"
    _write_case(d, cls="tool-using", expected="expected: {commit: 374478a}")
    res = vc.validate_case(d, REPO)
    assert res.valid is True
    assert res.complete is True


def test_placeholder_rubric_is_error(tmp_path):
    d = tmp_path / "2026-06-05-003-badrubric"
    _write_case(d, cls="reasoning", expected="expected: {max_score: 10}", extra_check=False)
    (d / "prompts" / "rubric.md").write_text("TODO 写 rubric", encoding="utf-8")
    res = vc.validate_case(d, REPO)
    assert res.valid is False
    assert any("rubric" in e for e in res.errors)


def test_missing_check_script_is_error(tmp_path):
    d = tmp_path / "2026-06-05-004-noscript"
    _write_case(d, cls="reasoning", expected="expected: {max_score: 10}")
    (d / "check.sh").unlink()  # 删掉 check.sh，但 case.yaml 仍声明 script
    res = vc.validate_case(d, REPO)
    assert res.valid is False
    assert any("check.script" in e for e in res.errors)
