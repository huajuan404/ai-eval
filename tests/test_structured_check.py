from __future__ import annotations

import json
import textwrap
from pathlib import Path

from bench.case import load_case
from bench.scoring import run_check


def _case(tmp_path: Path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "p.md").write_text("p", encoding="utf-8")
    (case_dir / "check.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: structured
            task:
              type: prompt
              prompt_file: p.md
            check:
              type: script
              script: check.sh
              report_file: evaluation.json
            """
        ),
        encoding="utf-8",
    )
    return load_case(case_dir)


def _report(passed: bool = True) -> dict:
    return {
        "schema_version": 1,
        "passed": passed,
        "summary": {"parse_ok": True},
        "items": [
            {
                "id": "1",
                "expected": "false",
                "actual": "false",
                "correct": True,
                "slices": {"product": "x"},
            }
        ],
        "errors": [],
    }


def test_structured_check_report_loaded(tmp_path: Path) -> None:
    case = _case(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    def run_fn(cmd, cwd, env):
        (Path(cwd) / "evaluation.json").write_text(
            json.dumps(_report()), encoding="utf-8"
        )
        return "ok", "", 0

    result = run_check(case, work, run_fn=run_fn)
    assert result.passed is True
    assert result.report["items"][0]["id"] == "1"


def test_structured_check_exit_report_mismatch_fails(tmp_path: Path) -> None:
    case = _case(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    def run_fn(cmd, cwd, env):
        (Path(cwd) / "evaluation.json").write_text(
            json.dumps(_report(False)), encoding="utf-8"
        )
        return "", "", 0

    result = run_check(case, work, run_fn=run_fn)
    assert result.passed is False
    assert "矛盾" in result.detail


def test_structured_check_missing_report_fails(tmp_path: Path) -> None:
    case = _case(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    result = run_check(case, work, run_fn=lambda cmd, cwd, env: ("", "", 0))
    assert result.passed is False
    assert "无法解析" in result.detail
