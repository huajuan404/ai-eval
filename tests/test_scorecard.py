"""U6: 计分卡生成 + 模型档案写入。"""

from __future__ import annotations

from pathlib import Path

from bench.orchestrator import MatrixResult, SkippedCell
from bench.record import Agentic, CheckResult, JudgeResult, RunRecord, Usage
from bench.scorecard import (
    build_scorecard,
    scorecard_filename,
    unique_scorecard_path,
    write_model_profile,
)


def test_scorecard_filename_single_case_runner() -> None:
    fn = scorecard_filename("2026-06-09", ["2026-06-09-001-codebase-insight-osd"], ["minimax-m3"])
    assert fn == "2026-06-09-codebase-insight-osd-minimax-m3.md"


def test_scorecard_filename_strips_date_seq_prefix() -> None:
    fn = scorecard_filename("2026-06-09", ["2026-06-02-001-fizzbuzz"], ["claude", "codex"])
    assert fn == "2026-06-09-fizzbuzz-claude+codex.md"


def test_scorecard_filename_many_runners_abbreviated() -> None:
    runners = ["qwen-0.5b-weak", "minimax-m3", "sonnet-4.6", "deepseek-v4", "glm-5.1"]
    fn = scorecard_filename("2026-06-09", ["2026-06-09-001-codebase-insight-osd"], runners)
    assert fn == "2026-06-09-codebase-insight-osd-5runners.md"


def test_scorecard_filename_multi_case_abbreviated() -> None:
    fn = scorecard_filename(
        "2026-06-09",
        ["2026-06-02-001-fizzbuzz", "2026-06-09-001-codebase-insight-osd"],
        ["claude"],
    )
    assert fn == "2026-06-09-fizzbuzz+1-claude.md"  # 按全名排序，06-02 在前


def test_scorecard_filename_dedup_runner_labels() -> None:
    # repeat>1 时 records 含重复 label，集合去重后不影响命名。
    fn = scorecard_filename("2026-06-09", ["2026-06-02-001-fizzbuzz"], ["claude", "claude"])
    assert fn == "2026-06-09-fizzbuzz-claude.md"


def test_unique_scorecard_path_appends_counter(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    assert unique_scorecard_path(tmp_path, "a.md") == tmp_path / "a-2.md"
    (tmp_path / "a-2.md").write_text("x", encoding="utf-8")
    assert unique_scorecard_path(tmp_path, "a.md") == tmp_path / "a-3.md"
    assert unique_scorecard_path(tmp_path, "fresh.md") == tmp_path / "fresh.md"


def _rec(
    case: str,
    label: str,
    *,
    model: str = "",
    duration: int = 500,
    usage: Usage | None = None,
    passed: bool | None = True,
    judge_score: float | None = None,
    judge_reasoning: str = "",
    files: int = 1,
    repeat_index: int = 0,
) -> RunRecord:
    return RunRecord(
        case=case,
        runner_label=label,
        launcher_type=label,
        runner_model=model,
        repeat_index=repeat_index,
        duration_ms=duration,
        usage=usage,
        agentic=Agentic(files_changed=files),
        check=CheckResult(ran=passed is not None, passed=passed),
        judge=JudgeResult(ran=True, model="claude", score=judge_score, reasoning=judge_reasoning)
        if judge_score is not None or judge_reasoning
        else None,
    )


def _mk_reasoning_case(root, name="t-1"):
    """建一个 reasoning 用例（task.md + input/ + case.yaml），供任务说明卡测试。"""
    from bench.case import load_case

    d = root / name
    (d / "prompts").mkdir(parents=True)
    (d / "input").mkdir()
    (d / "prompts" / "task.md").write_text(
        "# 工单线上问题判定\n\n你是工单分析员，给定工单输出结构化判定。\n", encoding="utf-8"
    )
    (d / "input" / "rules.txt").write_text("rules", encoding="utf-8")
    (d / "input" / "ticket.json").write_text("{}", encoding="utf-8")
    (d / "prompts" / "rubric.md").write_text("rubric", encoding="utf-8")
    (d / "case.yaml").write_text(
        f"name: {name}\nclass: reasoning\n"
        "task: {type: prompt, prompt_file: prompts/task.md}\n"
        "check: {type: none}\n"
        "judge: {enabled: true, rubric_file: prompts/rubric.md, dimensions: [correctness, evidence]}\n"
        "expected: {max_score: 20, passing_threshold: 14}\n",
        encoding="utf-8",
    )
    return load_case(str(d))


def test_scorecard_task_card_renders(tmp_path) -> None:
    case = _mk_reasoning_case(tmp_path)
    rec = _rec(case.name, "claude", judge_score=16)
    md = build_scorecard(MatrixResult(records=[rec]), judge_label="claude", cases={case.name: case})
    assert "### 📋 任务说明" in md
    assert "工单线上问题判定" in md                       # 标题
    assert "你是工单分析员" in md                          # 一句话简介
    assert "`rules.txt`" in md and "`ticket.json`" in md   # 输入资产
    assert "judge 4 维" not in md and "judge 2 维" in md    # 判分维度数
    assert "完成度=judge ≥ 14/20" in md                    # 完成度判据
    assert "完整输出" in md and "OUTPUT.txt" in md          # 输出指引


def test_scorecard_no_card_without_cases() -> None:
    # 不传 cases → 退化为旧版，无任务说明卡（向后兼容）
    rec = _rec("seed", "claude", judge_score=9)
    md = build_scorecard(MatrixResult(records=[rec]))
    assert "📋 任务说明" not in md


def test_scorecard_overview_table_multi_case(tmp_path) -> None:
    c1 = _mk_reasoning_case(tmp_path, "a-1")
    c2 = _mk_reasoning_case(tmp_path, "b-2")
    recs = [_rec("a-1", "claude", judge_score=16), _rec("b-2", "claude", judge_score=15)]
    md = build_scorecard(MatrixResult(records=recs), cases={"a-1": c1, "b-2": c2})
    assert "## 任务总览" in md
    assert "`a-1`" in md and "`b-2`" in md


def test_scorecard_three_runners_winners() -> None:
    records = [
        _rec("seed", "codex", duration=1000, usage=Usage.from_tokens(3300, 620, None), judge_score=9),
        _rec("seed", "claude", duration=500, usage=Usage.from_tokens(100, 50, 0.01), judge_score=7),
        _rec("seed", "glm-5.1", duration=800, usage=Usage.from_tokens(200, 80, 0.001), judge_score=5),
    ]
    md = build_scorecard(MatrixResult(records=records), judge_label="claude")
    # 三行 bundle
    assert "codex" in md and "claude" in md and "glm-5.1" in md
    # 每维赢家：质量=codex（judge 9），速度=claude（500ms），成本=glm-5.1（0.001）
    assert "质量=codex" in md
    assert "速度=claude" in md
    assert "成本=glm-5.1" in md


def test_scorecard_repeat_shows_spread() -> None:
    records = [
        _rec("seed", "codex", duration=d, repeat_index=i)
        for i, d in enumerate((400, 500, 600))
    ]
    md = build_scorecard(MatrixResult(records=records))
    assert "500 [400–600]" in md


def test_scorecard_null_usage_dash() -> None:
    records = [_rec("seed", "codex", usage=None)]
    md = build_scorecard(MatrixResult(records=records))
    # cost 与 tokens 列应有 —
    assert "—" in md


def test_scorecard_skipped_cell_na() -> None:
    res = MatrixResult(
        records=[_rec("seed", "codex")],
        skipped=[SkippedCell(case="seed", runner_label="glm-5.1", reason="requires_engine")],
    )
    md = build_scorecard(res)
    assert "glm-5.1" in md
    assert "N/A" in md


def test_scorecard_scrubs_judge_reasoning() -> None:
    records = [
        _rec("seed", "codex", judge_score=8, judge_reasoning="leaked sk-abcdefghij0123456789 token")
    ]
    md = build_scorecard(MatrixResult(records=records))
    assert "sk-abcdefghij0123456789" not in md
    assert "***REDACTED***" in md


def test_scorecard_overview_has_confound_and_judge() -> None:
    md = build_scorecard(MatrixResult(records=[_rec("seed", "codex")]), judge_label="claude")
    assert "混淆变量" in md
    assert "裁判" in md and "claude" in md
    assert "不自动聚合" in md


def test_write_model_profile_creates_and_appends(tmp_path: Path) -> None:
    p1 = write_model_profile(tmp_path, "codex", "seed 用例质量高、成本未知")
    assert p1.exists()
    content = p1.read_text(encoding="utf-8")
    assert "# codex" in content
    assert "## 评测记录" in content
    assert "seed 用例质量高" in content
    # 二次写入追加而非覆盖
    write_model_profile(tmp_path, "codex", "第二次记录")
    content2 = p1.read_text(encoding="utf-8")
    assert "seed 用例质量高" in content2
    assert "第二次记录" in content2
