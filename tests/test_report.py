"""HTML 报告：渲染内容、注入转义、离线重建。"""

from __future__ import annotations

import hashlib
import json
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from bench.__main__ import rebuild_report, run_benchmark
from bench.case import RunContract, load_case
from bench.comparison import ItemComparison, RunnerComparison
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


def _variant_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "cases" / "report-case"
    (case_dir / "prompts").mkdir(parents=True)
    (case_dir / "input").mkdir()
    (case_dir / "prompts" / "original.md").write_text(
        "original current definition", encoding="utf-8"
    )
    (case_dir / "prompts" / "v4.md").write_text(
        "v4 current definition", encoding="utf-8"
    )
    (case_dir / "input" / "seed.txt").write_text("x", encoding="utf-8")
    (case_dir / "check.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: 2
            task:
              variants:
                original: prompts/original.md
                v4: prompts/v4.md
              default: v4
            check: check.sh
            """
        ),
        encoding="utf-8",
    )
    return case_dir


def _prompt_record(
    tmp_path: Path,
    variant: str,
    *,
    template: str,
    actual_input: str | None,
    repeat: int = 0,
    runner: str = "fixture-runner",
    declared_units: tuple[str, ...] | None = ("domain-001",),
) -> RunRecord:
    artifacts = tmp_path / "artifacts" / runner / variant / f"repeat-{repeat}"
    artifacts.mkdir(parents=True)
    (artifacts / "PROMPT.txt").write_text(template, encoding="utf-8")
    if declared_units is not None:
        (artifacts / "batch_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "batches": [{"id": unit_id} for unit_id in declared_units],
                }
            ),
            encoding="utf-8",
        )
    (artifacts / "RUN_CONTEXT.json").write_text(
        json.dumps(
            {
                "variant": {
                    "label": variant,
                    "parameters": {
                        "records_renderer": (
                            "legacy_single_line"
                            if variant == "original"
                            else "bounded_product_v4"
                        )
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    run_context_sha256 = hashlib.sha256(
        (artifacts / "RUN_CONTEXT.json").read_bytes()
    ).hexdigest()
    request_manifest_file = ""
    request_manifest_sha256 = ""
    if actual_input is not None:
        requests = artifacts / "request_payloads"
        requests.mkdir()
        payload = {
            "model": "fixture-model",
            "messages": [{"role": "user", "content": actual_input}],
        }
        (requests / "domain-001.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        request_hash = hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        request_manifest = {
            "schema_version": 2,
            "provider": {
                "endpoint": "https://provider.invalid",
                "requested_model": "fixture-model",
                "request_config_sha256": "a" * 64,
                "credentials_present": True,
            },
            "runtime_sha256": "b" * 64,
            "variant": {
                "label": variant,
                "parameters_sha256": "c" * 64,
            },
            "requests": [
                {
                    "unit_id": "domain-001",
                    "input_sha256": "d" * 64,
                    "request_sha256": request_hash,
                    "status_code": 200,
                    "auth_succeeded": True,
                    "returned_model": "fixture-model",
                }
            ],
        }
        request_manifest_path = artifacts / "request_manifest.json"
        request_manifest_path.write_text(
            json.dumps(request_manifest), encoding="utf-8"
        )
        request_manifest_file = request_manifest_path.name
        request_manifest_sha256 = hashlib.sha256(
            request_manifest_path.read_bytes()
        ).hexdigest()
    return RunRecord(
        case="report-case",
        runner_label=runner,
        launcher_type="command",
        run_id="run-x",
        variant_label=variant,
        prompt_template_sha256=hashlib.sha256(template.encode("utf-8")).hexdigest(),
        run_context_sha256=run_context_sha256,
        repeat_index=repeat,
        check=CheckResult(ran=True, passed=True),
        artifacts_dir=str(artifacts),
        request_manifest_file=request_manifest_file,
        request_manifest_sha256=request_manifest_sha256,
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
    assert 'class="result-cell ok"' in html
    assert 'class="result-cell bad"' in html
    assert 'class="result-grid"' in html
    # cell 相对链接指向 runs 目录内部
    assert "cells/report-case/default/fast/repeat-0/raw.txt" in html
    # 外部依赖为零：无 script 标签、无 http 资源引用
    assert "<script" not in html
    assert 'src="http' not in html and 'href="http' not in html


def test_report_escapes_untrusted_judge_reasoning(tmp_path: Path) -> None:
    case = load_case(_case(tmp_path))
    evil = '<script>alert("pwn")</script>\napi_key=reasoning-secret'
    record = replace(
        _record("fast", reasoning=evil), runner_model="api_key=model-secret"
    ).with_check(
        CheckResult(ran=True, passed=True, detail="api_key=check-secret")
    )
    html = build_report_html(
        run_id="run-x",
        records=[record],
        cases={"report-case": case},
        comparisons={},
        judge_label="api_key=judge-secret",
    )
    assert evil not in html
    assert "&lt;script&gt;" in html
    assert "reasoning-secret" not in html
    assert "check-secret" not in html
    assert "model-secret" not in html
    assert "judge-secret" not in html
    assert "***REDACTED***" in html


def test_report_renders_compared_prompt_templates_and_actual_inputs(
    tmp_path: Path,
) -> None:
    case = load_case(_variant_case(tmp_path))
    original = _prompt_record(
        tmp_path,
        "original",
        template="historical original template\nshared tail",
        actual_input="original rendered ticket\nFULL_ORIGINAL_TAIL",
    )
    v4 = _prompt_record(
        tmp_path,
        "v4",
        template="historical v4 template\nshared tail",
        actual_input="v4 <script> rendered ticket\nFULL_V4_TAIL",
    )

    rendered = build_report_html(
        run_id="run-x",
        records=[original, v4],
        cases={"report-case": case},
        comparisons={},
    )

    assert "Prompt / 实际输入对比" in rendered
    assert "legacy_single_line" in rendered
    assert "bounded_product_v4" in rendered
    assert "historical original template" in rendered
    assert "historical v4 template" in rendered
    assert "original current definition" not in rendered
    assert "模板差异：original → v4" in rendered
    assert "实际输入差异：domain-001 · original → v4" in rendered
    assert "FULL_ORIGINAL_TAIL" in rendered
    assert "FULL_V4_TAIL" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<details open>" not in rendered
    assert 'class="diff-line del"' in rendered
    assert 'class="diff-line add"' in rendered
    assert 'class="diff-line hunk"' in rendered
    assert "--diff-add-bg:#dafbe1" in rendered
    assert "--diff-del-bg:#ffebe9" in rendered


def test_report_adapts_to_variant_case_without_request_payloads(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            variant,
            template=f"{variant} generic prompt",
            actual_input=None,
            declared_units=None,
        )
        for variant in ("original", "v4")
    ]

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "original generic prompt" in rendered
    assert "v4 generic prompt" in rendered
    assert "实际请求体未采集" in rendered
    assert "证据不完整" not in rendered
    assert "模板完整 · 请求未采集" in rendered


def test_report_marks_empty_request_payload_directory_incomplete(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            variant,
            template=f"{variant} prompt",
            actual_input=None,
            declared_units=None,
        )
        for variant in ("original", "v4")
    ]
    for record in records:
        (Path(record.artifacts_dir) / "request_payloads").mkdir()

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "request_payloads/ 目录为空" in rendered
    assert "证据不完整" in rendered


def test_report_marks_cross_variant_payload_unit_mismatch(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    original = _prompt_record(
        tmp_path,
        "original",
        template="original prompt",
        actual_input="domain input",
        declared_units=None,
    )
    gateway_payload = Path(original.artifacts_dir) / "request_payloads/gateway-001.json"
    gateway_payload.write_text(
        json.dumps({"prompt": "gateway input"}), encoding="utf-8"
    )
    v4 = _prompt_record(
        tmp_path,
        "v4",
        template="v4 prompt",
        actual_input="domain input",
        declared_units=None,
    )

    rendered = build_report_html(
        run_id="run-x",
        records=[original, v4],
        cases={"report-case": case},
        comparisons={},
    )

    assert "original 与 v4 的实际输入 batch 集合不一致" in rendered
    assert "证据不完整" in rendered


def test_report_marks_cross_variant_manifest_coverage_mismatch(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            "original",
            template="original prompt",
            actual_input="domain input",
        ),
        _prompt_record(
            tmp_path,
            "v4",
            template="v4 prompt",
            actual_input="domain input",
            declared_units=None,
        ),
    ]

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "original 与 v4 的 batch_manifest 覆盖不一致" in rendered
    assert "证据不完整" in rendered


def test_report_diff_exposes_terminal_newline_only_change(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            "original",
            template="same content",
            actual_input="same input",
        ),
        _prompt_record(
            tmp_path,
            "v4",
            template="same content\n",
            actual_input="same input",
        ),
    ]

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "No newline at end of file" in rendered
    assert 'class="diff-line meta"' in rendered


def test_report_exposes_prompt_input_drift_and_missing_evidence(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            "original",
            template="template-a",
            actual_input="rendered-a",
            repeat=0,
        ),
        _prompt_record(
            tmp_path,
            "original",
            template="template-b",
            actual_input="rendered-b",
            repeat=1,
        ),
        _prompt_record(
            tmp_path,
            "v4",
            template="template-v4",
            actual_input=None,
        ),
    ]

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "检测到模板漂移" in rendered
    assert "检测到实际输入漂移" in rendered
    assert "缺少声明 batch: domain-001" in rendered
    assert "template-a" in rendered and "template-b" in rendered
    assert "rendered-a" in rendered and "rendered-b" in rendered


def test_report_marks_prompt_and_request_payload_hash_tampering(tmp_path: Path) -> None:
    case = replace(
        load_case(_variant_case(tmp_path)),
        run_contract=RunContract(
            request_manifest_file="request_manifest.json",
            request_manifest_required=True,
        ),
    )
    records = [
        _prompt_record(
            tmp_path,
            variant,
            template=f"{variant} prompt",
            actual_input=f"{variant} input",
        )
        for variant in ("original", "v4")
    ]
    for index, record in enumerate(records):
        artifacts = Path(record.artifacts_dir)
        payload = json.loads(
            (artifacts / "request_payloads/domain-001.json").read_text(encoding="utf-8")
        )
        request_hash = hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        request_manifest = {
            "schema_version": 2,
            "provider": {
                "endpoint": "https://provider.invalid",
                "requested_model": "fixture-model",
                "request_config_sha256": "a" * 64,
                "credentials_present": True,
            },
            "runtime_sha256": "b" * 64,
            "variant": {
                "label": record.variant_label,
                "parameters_sha256": "c" * 64,
            },
            "requests": [
                {
                    "unit_id": "domain-001",
                    "input_sha256": "d" * 64,
                    "request_sha256": request_hash,
                    "status_code": 200,
                    "auth_succeeded": True,
                    "returned_model": "fixture-model",
                }
            ],
        }
        request_manifest_path = artifacts / "request_manifest.json"
        request_manifest_path.write_text(
            json.dumps(request_manifest), encoding="utf-8"
        )
        records[index] = replace(
            record,
            request_manifest_file="request_manifest.json",
            request_manifest_sha256=hashlib.sha256(
                request_manifest_path.read_bytes()
            ).hexdigest(),
        )
    case = replace(
        case,
        run_contract=RunContract(
            request_manifest_file="renamed-after-run.json",
            request_manifest_required=True,
        ),
    )
    Path(records[0].artifacts_dir, "PROMPT.txt").write_text(
        "tampered prompt", encoding="utf-8"
    )
    Path(records[0].artifacts_dir, "request_payloads/domain-001.json").write_text(
        json.dumps({"messages": [{"role": "user", "content": "tampered input"}]}),
        encoding="utf-8",
    )
    Path(records[0].artifacts_dir, "RUN_CONTEXT.json").write_text(
        json.dumps(
            {"variant": {"parameters": {"renderer": "tampered context"}}}
        ),
        encoding="utf-8",
    )

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "PROMPT.txt 与 run record hash 不一致" in rendered
    assert "RUN_CONTEXT.json 与 run record hash 不一致" in rendered
    assert "请求体与 request manifest hash 不一致" in rendered
    assert "tampered prompt" not in rendered
    assert "tampered context" not in rendered
    assert "tampered input" not in rendered
    assert "证据不完整" in rendered


def test_report_refuses_unanchored_legacy_request_payload(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    record = replace(
        _prompt_record(
            tmp_path,
            "original",
            template="original prompt",
            actual_input="unanchored legacy input",
        ),
        request_manifest_file="",
        request_manifest_sha256="",
    )
    candidate = _prompt_record(
        tmp_path,
        "v4",
        template="candidate prompt",
        actual_input="anchored candidate input",
    )

    rendered = build_report_html(
        run_id="run-x",
        records=[record, candidate],
        cases={"report-case": case},
        comparisons={},
    )

    assert "run record 未记录 request manifest" in rendered
    assert "unanchored legacy input" not in rendered
    assert "证据不完整" in rendered


def test_report_marks_partially_missing_request_batches_incomplete(
    tmp_path: Path,
) -> None:
    case = load_case(_variant_case(tmp_path))
    original = _prompt_record(
        tmp_path,
        "original",
        template="original",
        actual_input="domain input",
        declared_units=("domain-001", "gateway-001"),
    )
    v4 = _prompt_record(
        tmp_path,
        "v4",
        template="v4",
        actual_input="domain input",
        declared_units=("domain-001", "gateway-001"),
    )

    rendered = build_report_html(
        run_id="run-x",
        records=[original, v4],
        cases={"report-case": case},
        comparisons={},
    )

    assert "缺少声明 batch: gateway-001" in rendered
    assert "证据不完整" in rendered


def test_report_marks_mixed_batch_manifest_presence_incomplete(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            "original",
            template="original",
            actual_input="domain input",
            repeat=0,
        ),
        _prompt_record(
            tmp_path,
            "original",
            template="original",
            actual_input="domain input",
            repeat=1,
            declared_units=None,
        ),
        _prompt_record(
            tmp_path,
            "v4",
            template="v4",
            actual_input="domain input",
        ),
    ]

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "batch_manifest.json 缺失，无法核对声明 batch" in rendered
    assert "证据不完整" in rendered


def test_report_compares_variants_within_each_runner(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    records = [
        _prompt_record(
            tmp_path,
            variant,
            runner=runner,
            template=f"{variant} template",
            actual_input=f"{runner} wrapper + {variant} input",
        )
        for runner in ("runner-a", "runner-b")
        for variant in ("original", "v4")
    ]

    rendered = build_report_html(
        run_id="run-x",
        records=records,
        cases={"report-case": case},
        comparisons={},
    )

    assert "检测到实际输入漂移" not in rendered
    assert "runner-a@original" in rendered and "runner-b@v4" in rendered
    assert "runner=runner-a" in rendered and "runner=runner-b" in rendered


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
    assert '<span class="signal">1/1</span>' in html
    assert '<span class="signal">0/1</span>' in html


def test_report_separates_execution_failure_from_check_failure(tmp_path: Path) -> None:
    case = load_case(_case(tmp_path))
    failed = RunRecord(
        case="report-case",
        runner_label="fast",
        launcher_type="command",
        is_error=True,
        check=CheckResult(
            ran=True,
            passed=False,
            detail="无法读取 predictions.jsonl",
            report={
                "items": [
                    {"id": "fake-missing", "correct": False},
                ]
            },
        ),
        judge=JudgeResult(ran=True, model="stale", score=0, reasoning="stale judge"),
    )
    healthy = _record("fast").with_check(
        CheckResult(
            ran=True,
            passed=True,
            report={
                "items": [
                    {"id": "real-item", "correct": True},
                ]
            },
        )
    )
    html = build_report_html(
        run_id="run-x",
        records=[failed, healthy],
        cases={"report-case": case},
        comparisons={},
    )

    assert "执行成功</th>" in html
    assert "1/2" in html
    assert "check 跳过" in html
    assert "runner 执行失败，未进入 check / judge" in html
    assert "无法读取 predictions.jsonl" not in html
    assert "judge 0" not in html and "stale judge" not in html
    assert "fake-missing" not in html
    assert "real-item" in html


def test_report_renders_structure_and_semantic_check_axes(tmp_path: Path) -> None:
    case = load_case(_case(tmp_path))
    record = _record("fast", passed=False).with_check(
        CheckResult(
            ran=True,
            passed=False,
            report={
                "schema_version": 1,
                "passed": False,
                "summary": {
                    "structure": {
                        "compliant": False,
                        "strict_batches": 0,
                        "total_batches": 2,
                    },
                    "semantic": {
                        "evaluated_count": 9,
                        "expected_count": 9,
                        "correct_count": 8,
                        "coverage": 1.0,
                        "accuracy": 8 / 9,
                    },
                },
                "items": [
                    {
                        "id": "real-item",
                        "expected": "false",
                        "actual": "false",
                        "correct": True,
                        "evaluated": True,
                    }
                ],
                "errors": ["结构失败"],
            },
        )
    )

    html = build_report_html(
        run_id="run-x",
        records=[record],
        cases={"report-case": case},
        comparisons={},
    )

    assert "结构与结论双轴" in html
    assert "结构合规" in html and "0/1" in html
    assert "结论覆盖" in html and "1/1" in html
    assert "结论正确" in html and "1/1" in html


def test_report_renders_value_diff_with_full_ticket_and_git_colors(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    original = _prompt_record(
        tmp_path, "original", template="original", actual_input="same input"
    )
    candidate = _prompt_record(
        tmp_path, "v4", template="v4", actual_input="same input"
    )
    ticket = {
        "id": "42",
        "team": "应用接入平台",
        "product_display_name": "网关 api_key=product-secret",
        "problem_analysis": "线上请求失败 api_key=super-secret-ticket",
    }
    original_prediction = {
        "id": "42",
        "is_online_issue": "false",
        "brief": "用户配置错误 Bearer abcdefghijklmnop",
    }
    candidate_prediction = {
        "id": "42",
        "is_online_issue": "true",
        "brief": "网关回归",
    }
    original = original.with_check(
        CheckResult(
            ran=True,
            passed=True,
            report={"items": [{"id": "42", "actual": "false"}]},
        )
    )
    candidate = candidate.with_check(
        CheckResult(
            ran=True,
            passed=True,
            report={"items": [{"id": "42", "actual": "true"}]},
        )
    )
    for record in (original, candidate):
        artifacts = Path(record.artifacts_dir)
        dataset_path = artifacts / "dataset.jsonl"
        dataset_path.write_text(
            json.dumps(ticket, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        dataset_bytes = dataset_path.read_bytes()
        input_manifest = [
            {
                "path": "dataset.jsonl",
                "size": len(dataset_bytes),
                "file_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            }
        ]
        (artifacts / "input-manifest.json").write_text(
            json.dumps(input_manifest, ensure_ascii=False), encoding="utf-8"
        )
        input_manifest_sha256 = hashlib.sha256(
            json.dumps(
                input_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if record is original:
            original = replace(
                original, input_manifest_sha256=input_manifest_sha256
            )
        else:
            candidate = replace(
                candidate, input_manifest_sha256=input_manifest_sha256
            )

    def write_response(record: RunRecord, prediction: dict[str, str]) -> tuple[RunRecord, bytes]:
        responses = Path(record.artifacts_dir) / "responses"
        responses.mkdir()
        response_path = responses / "domain-001.json"
        response_path.write_text(
            json.dumps(
                {
                    "content": [
                        {
                            "text": "```json\n"
                            + json.dumps([prediction], ensure_ascii=False)
                            + "\n```"
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        response_bytes = response_path.read_bytes()
        return (
            replace(
                record,
                response_sha256={
                    "domain-001.json": hashlib.sha256(response_bytes).hexdigest()
                },
            ),
            response_bytes,
        )

    original, original_response_bytes = write_response(original, original_prediction)
    candidate, candidate_response_bytes = write_response(candidate, candidate_prediction)
    responses = Path(original.artifacts_dir) / "responses"
    comparison = RunnerComparison(
        runner="fixture-runner",
        baseline="original",
        candidate="v4",
        method="item_value_diff",
        repeats=1,
        changed=1,
        rows=(
            ItemComparison(
                item_id="42",
                baseline_correct=0,
                candidate_correct=0,
                baseline_disagreement=0,
                candidate_disagreement=0,
                status="changed",
                baseline_value="false",
                candidate_value="true",
                reference_value="false",
            ),
        ),
    )

    html = build_report_html(
        run_id="run-x",
        records=[original, candidate],
        cases={"report-case": case},
        comparisons={"report-case": [comparison]},
    )

    assert "数据库 baseline 快照仅用于抽样分层和参照，不是真值" in html
    assert "应用接入平台" in html and "线上请求失败" in html
    assert "用户配置错误" in html and "网关回归" in html
    assert "super-secret-ticket" not in html
    assert "product-secret" not in html
    assert "abcdefghijklmnop" not in html
    assert "***REDACTED***" in html
    assert 'diff-line del' in html and 'diff-line add' in html
    assert "红/绿仅表示删除/新增，不代表错误/正确" in html
    assert "工单 <code>42</code> · repeat-0" in html
    assert "双方完整对象均从本次原始 response 恢复" in html

    response_envelope = json.loads(
        (responses / "domain-001.json").read_text(encoding="utf-8")
    )
    response_envelope["content"][0]["text"] = response_envelope["content"][0][
        "text"
    ].replace("用户配置错误", "被篡改但分类不变")
    (responses / "domain-001.json").write_text(
        json.dumps(response_envelope, ensure_ascii=False), encoding="utf-8"
    )
    response_tampered = build_report_html(
        run_id="run-x",
        records=[original, candidate],
        cases={"report-case": case},
        comparisons={"report-case": [comparison]},
    )
    assert "与 run record response hash 不一致" in response_tampered
    assert "被篡改但分类不变" not in response_tampered
    (responses / "domain-001.json").write_bytes(original_response_bytes)

    candidate_response_path = Path(candidate.artifacts_dir, "responses/domain-001.json")
    candidate_response_envelope = json.loads(candidate_response_bytes)
    candidate_response_envelope["content"][0]["text"] = candidate_response_envelope[
        "content"
    ][0]["text"].replace('"is_online_issue": "true"', '"is_online_issue": "false"')
    candidate_response_path.write_text(
        json.dumps(candidate_response_envelope, ensure_ascii=False), encoding="utf-8"
    )
    candidate_mismatch = replace(
        candidate,
        response_sha256={
            "domain-001.json": hashlib.sha256(
                candidate_response_path.read_bytes()
            ).hexdigest()
        },
    )
    tampered = build_report_html(
        run_id="run-x",
        records=[original, candidate_mismatch],
        cases={"report-case": case},
        comparisons={"report-case": [comparison]},
    )
    assert "完整输出与 check actual 不一致" in tampered
    assert "查看完整工单输入" not in tampered


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
    run_json = next(layout.cells_dir.glob("*/*/*/repeat-*/run.json"))
    serialized = json.loads(run_json.read_text(encoding="utf-8"))
    serialized["artifacts_dir"] = "/tmp/untrusted-stale-artifacts"
    run_json.write_text(json.dumps(serialized), encoding="utf-8")
    rebound = load_run_records(layout)
    assert rebound[0].artifacts_dir == str(run_json.parent / "artifacts")
    with pytest.raises(ReportError, match="找不到 run"):
        find_run_layout("nope-123", [root])


def test_run_benchmark_does_not_resolve_judge_when_all_runs_fail(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    case_dir = _case(root, "failed-judge-case")
    (case_dir / "prompts" / "rubric.md").write_text("rubric", encoding="utf-8")
    with (case_dir / "case.yaml").open("a", encoding="utf-8") as f:
        f.write(
            "judge:\n"
            "  rubric: prompts/rubric.md\n"
            "  dimensions: [correctness]\n"
        )
    registry = {"fake": RunnerProfile("fake", "command", template="exit 1")}
    cfg = RunConfig(
        runners=("fake",),
        cases=("failed-judge-case",),
        judge="missing-judge",
        workers=1,
    )

    run_benchmark(
        cfg,
        registry,
        root,
        run_fn=lambda c, w, e: ("", "runner failed", 1),
        judge_run_fn=lambda c, w, e: pytest.fail("judge 不应运行"),
    )

    run_dirs = list((root / "runs").iterdir())
    assert len(run_dirs) == 1
    records = load_run_records(find_run_layout(run_dirs[0].name, [root]))
    assert len(records) == 1
    assert records[0].is_error is True
    assert records[0].check.ran is False
    assert records[0].judge is None
