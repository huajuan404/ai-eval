"""HTML 报告：渲染内容、注入转义、离线重建。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench.__main__ import rebuild_report, run_benchmark
from bench.case import load_case
from bench.config import RunConfig
from bench.record import CheckResult, JudgeResult, RunRecord, Usage
from bench.registry import RunnerProfile
from bench.report import ReportError, build_report_html, find_run_layout, load_run_records


def _case(tmp_path: Path, name: str = "report-case") -> Path:
    d = tmp_path / "cases" / name
    (d / "prompts").mkdir(parents=True)
    (d / "input").mkdir()
    (d / "prompts" / "task.md").write_text("# 报告任务\n\n做点什么", encoding="utf-8")
    (d / "input" / "seed.txt").write_text("x", encoding="utf-8")
    (d / "check.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (d / "case.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: 2
            task: prompts/task.md
            check: check.sh
            """
        ),
        encoding="utf-8",
    )
    return d


def _record(runner: str, *, passed: bool = True, reasoning: str = "") -> RunRecord:
    return RunRecord(
        case="report-case",
        runner_label=runner,
        launcher_type="command",
        run_id="run-x",
        duration_ms=1200,
        usage=Usage.from_tokens(100, 50),
        check=CheckResult(ran=True, passed=passed, detail="check output"),
        judge=JudgeResult(ran=True, model="j", score=8, max=10, reasoning=reasoning)
        if reasoning
        else None,
        artifacts_dir="/tmp/x",
    )


def test_report_html_renders_summary_and_cells(tmp_path: Path) -> None:
    case = load_case(_case(tmp_path))
    html = build_report_html(
        run_id="run-x",
        records=[_record("fast"), _record("slow", passed=False)],
        cases={"report-case": case},
        comparisons={},
        judge_label="claude",
    )
    assert "评测报告" in html
    assert "run-x" in html
    assert "fast" in html and "slow" in html
    assert "任务完成率总览" in html
    # 完成/未完成 badge 都出现（fast pass，slow fail）
    assert "✅ 完成" in html and "❌ 未完成" in html
    # cell 相对链接指向 runs 目录内部
    assert "cells/report-case/default/fast/repeat-0/raw.txt" in html
    # 外部依赖为零：无 script 标签、无 http 资源引用
    assert "<script" not in html
    assert 'src="http' not in html and 'href="http' not in html


def test_report_escapes_untrusted_judge_reasoning(tmp_path: Path) -> None:
    case = load_case(_case(tmp_path))
    evil = '<script>alert("pwn")</script>'
    html = build_report_html(
        run_id="run-x",
        records=[_record("fast", reasoning=evil)],
        cases={"report-case": case},
        comparisons={},
    )
    assert evil not in html
    assert "&lt;script&gt;" in html


def test_report_items_grid_from_check_report(tmp_path: Path) -> None:
    case = load_case(_case(tmp_path))
    record = _record("fast").with_check(
        CheckResult(
            ran=True,
            passed=False,
            report={
                "schema_version": 1,
                "passed": False,
                "summary": {},
                "errors": [],
                "items": [
                    {"id": "t-1", "expected": "P0", "actual": "P0", "correct": True},
                    {"id": "t-2", "expected": "P1", "actual": "P2", "correct": False},
                ],
            },
        )
    )
    html = build_report_html(
        run_id="run-x",
        records=[record],
        cases={"report-case": case},
        comparisons={},
    )
    assert "数据轴：item 级明细" in html
    assert "t-1" in html and "t-2" in html


def test_run_benchmark_writes_report_and_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _case(root, "smoke-case")
    registry = {"fake": RunnerProfile("fake", "command", template="echo hi")}
    cfg = RunConfig(runners=("fake",), cases=("smoke-case",), judge="fake", workers=1)

    def fake_run(cmd, cwd, env):
        Path(cwd, "answer.txt").write_text("hi", encoding="utf-8")
        return "final", "", 0

    run_benchmark(cfg, registry, root, run_fn=fake_run, judge_run_fn=fake_run)
    run_dirs = list((root / "runs").iterdir())
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name
    report = run_dirs[0] / "report.html"
    assert report.is_file()
    assert "smoke-case" in report.read_text(encoding="utf-8")

    # 离线重建：删掉后可从磁盘恢复
    report.unlink()
    rebuilt = rebuild_report(run_id, root)
    assert rebuilt == report and report.is_file()

    layout = find_run_layout(run_id, [root])
    assert len(load_run_records(layout)) == 1
    with pytest.raises(ReportError, match="找不到 run"):
        find_run_layout("nope-123", [root])
