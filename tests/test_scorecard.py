"""U6: 计分卡生成 + 模型档案写入。"""

from __future__ import annotations

from pathlib import Path

from bench.orchestrator import MatrixResult, SkippedCell
from bench.record import Agentic, CheckResult, JudgeResult, RunRecord, Usage
from bench.scorecard import build_scorecard, write_model_profile


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
