"""bench CLI 入口（R15/R16，KTD1）。

  python3 -m bench            # 按 config.yaml 跑全部
  python3 -m bench -l         # 列出可用用例与 runner 档案
  python3 -m bench -c <case> -r codex,claude   # 指定用例与 runner（覆盖 config）
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from datetime import date
from pathlib import Path

from .adapters import get_adapter
from .case import CaseError, discover_cases, is_private_case, resolve_case_roots
from .comparison import ComparisonError, compare_run_variants
from .config import ConfigError, RunConfig, load_config
from .layout import RunLayout
from .log import configure as configure_log
from .log import get_logger
from .orchestrator import (
    MatrixResult,
    OrchestratorError,
    RunPlan,
    execute_plan,
    generate_run_id,
    plan_matrix,
    run_subprocess,
)
from .record import RunRecord
from .registry import RegistryError, get_profile, load_registry
from .report import (
    ReportError,
    build_report_html,
    find_run_layout,
    load_run_records,
)
from .scorecard import (
    build_scorecard,
    scorecard_filename,
    unique_scorecard_path,
    write_model_profile,
)
from .scoring import _default_script_runner, score_record
from .run_manifest import (
    RunManifestError,
    snapshot_case_integrity,
    validate_provider_invariants,
)
from .scrub import scrub_text

ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description="可换模型的端到端 agent benchmark")
    p.add_argument("-c", "--case", action="append", default=[], help="只跑指定用例（可多次）")
    p.add_argument("-r", "--runners", default=None, help="逗号分隔的 runner 标签，覆盖 config")
    p.add_argument(
        "--variants",
        default=None,
        help="逗号分隔的 prompt variant；'*' 表示所选 case 的全部版本",
    )
    p.add_argument("-j", "--judge", default=None, help="裁判档案标签，覆盖 config")
    p.add_argument("--repeat", type=int, default=None, help="每格运行次数（默认 1；显式传 N 才跑多次）")
    p.add_argument(
        "-w", "--workers", type=int, default=0,
        help="并发工作线程数（默认自动 = min(provider 数, 6)；-w 1 强制串行）",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="只输出警告与最终汇总（压低 start/done/progress）")
    p.add_argument("-l", "--list", action="store_true", help="列出可用用例与 runner 档案")
    p.add_argument(
        "--write-profiles",
        action="store_true",
        help="把本次各 runner 的表现写入 models/<label>.md",
    )
    p.add_argument(
        "--report",
        metavar="RUN_ID",
        default=None,
        help="不跑评测，对已完成的 run 重新生成 HTML 报告（runs/<run_id>/report.html）",
    )
    return p


def merge_config(base: RunConfig, args: argparse.Namespace) -> RunConfig:
    """CLI 覆盖 config：提供的字段覆盖，未提供则沿用 config。"""
    # workers：--workers 显式值 > config.yaml 值 > 0（run_matrix 会解析为自动）
    workers = args.workers if args.workers > 0 else base.workers
    variants = base.variants
    if args.variants is not None:
        parts = [part.strip() for part in args.variants.split(",")]
        if any(not part for part in parts):
            raise ConfigError("--variants 不能包含空 label。")
        if len(parts) != len(set(parts)):
            raise ConfigError("--variants 不能包含重复 label。")
        variants = tuple(parts)
    return RunConfig(
        runners=tuple(s.strip() for s in args.runners.split(",")) if args.runners else base.runners,
        cases=tuple(args.case) if args.case else base.cases,
        variants=variants,
        judge=args.judge or base.judge,
        repeat=args.repeat if args.repeat is not None else base.repeat,
        workers=workers,
        dimensions=base.dimensions,
    )


def cmd_list(registry: dict, cases: list) -> str:
    lines = ["可用 runner 档案:"]
    for label in sorted(registry):
        prof = registry[label]
        detail = prof.model or (f"c {prof.config}" if prof.config else prof.launcher)
        lines.append(f"  {label}  ({prof.launcher}: {detail})")
    lines.append("")
    lines.append("可用用例:")
    if cases:
        for c in cases:
            tag = f" [requires_engine={c.requires_engine}]" if c.requires_engine else ""
            variants = (
                f" [variants={','.join(c.task.variant_labels)} default={c.task.default_variant}]"
                if c.task.variant_labels != ("default",)
                else ""
            )
            priv = " 🔒私有" if is_private_case(c, ROOT) else ""
            lines.append(f"  {c.name}  ({c.task.type}){tag}{variants}{priv}")
    else:
        lines.append("  (无；在 cases/ 下创建带 case.yaml 的用例)")
    return "\n".join(lines)


def _profile_summary(label: str, records: list) -> str:
    """为某 runner 生成一行档案摘要。"""
    bits = []
    for rec in records:
        check = "pass" if rec.check.passed else ("fail" if rec.check.ran else "—")
        jscore = rec.judge.score if rec.judge and rec.judge.score is not None else "—"
        bits.append(
            f"{rec.case}@{rec.variant_label}: check={check}, judge={jscore}, {rec.duration_ms}ms"
        )
    return "；".join(bits)


def _report_context(
    selected_cases: list, root: Path, write_profiles: bool
) -> tuple[bool, Path]:
    private_flags = [is_private_case(case, root) for case in selected_cases]
    if any(private_flags) and not all(private_flags):
        raise OrchestratorError("一次运行不能混合公开 case 与私有 case。")
    if not private_flags or not any(private_flags):
        return False, root
    case_roots = {case.directory.parent.resolve() for case in selected_cases}
    if len(case_roots) != 1:
        raise OrchestratorError("一次私有运行的所有 case 必须来自同一个私有 cases root。")
    if write_profiles:
        raise OrchestratorError("私有 case 运行禁止 --write-profiles，避免写入公开 models/。")
    return True, next(iter(case_roots)).parent


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _run_benchmark_impl(
    config: RunConfig,
    registry: dict,
    root: Path,
    plan: RunPlan,
    is_private: bool,
    report_root: Path,
    *,
    run_fn=run_subprocess,
    judge_run_fn=run_subprocess,
    check_run_fn=_default_script_runner,
    write_profiles: bool = False,
) -> Path:
    """执行矩阵 → 判分 → 计分卡，返回计分卡路径。"""
    run_id = plan.run_id
    selected_cases = list(plan.selected_cases)
    layout = RunLayout(report_root, run_id)
    locks_before = {
        case.name: snapshot_case_integrity(case) for case in selected_cases
    }
    input_hashes = {
        case_name: snapshot.input_sha256
        for case_name, snapshot in locks_before.items()
    }
    protected_before = {
        case_name: snapshot.protected_files
        for case_name, snapshot in locks_before.items()
    }
    integrity_verified = {
        case_name: snapshot.declared_files
        for case_name, snapshot in locks_before.items()
    }
    _write_json(
        layout.case_lock_path,
        {
            "schema_version": 1,
            "cases": {
                case_name: snapshot.to_dict()
                for case_name, snapshot in locks_before.items()
            },
        },
    )
    effective_variants: dict[str, list[str]] = {
        case.name: [] for case in selected_cases
    }
    for cell in plan.cells:
        labels = effective_variants[cell.case.name]
        if cell.variant_label not in labels:
            labels.append(cell.variant_label)
    for cell in plan.skipped:
        labels = effective_variants[cell.case]
        if cell.variant_label not in labels:
            labels.append(cell.variant_label)
    manifest: dict = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "private": is_private,
        "judge": config.judge,
        "selection": {
            "cases": [case.name for case in selected_cases],
            "runners": list(plan.selected_runners),
            "variants": effective_variants,
            "repeat": {
                case.name: case.repeat if case.repeat is not None else config.repeat
                for case in selected_cases
            },
            "workers": plan.workers,
            "workers_auto": plan.workers_auto,
        },
        "input_manifest_sha256": input_hashes,
        "integrity_verified": integrity_verified,
        "protected_before": protected_before,
        "case_locks": {
            case_name: snapshot.lock_sha256
            for case_name, snapshot in locks_before.items()
        },
        "schedule_seed": run_id,
        "schedule": list(plan.schedule),
        "skipped": [
            {
                "case": cell.case,
                "runner": cell.runner_label,
                "variant": cell.variant_label,
                "reason": cell.reason,
            }
            for cell in plan.skipped
        ],
    }
    _write_json(layout.manifest_path, manifest)
    result = execute_plan(
        plan,
        layout,
        run_fn=run_fn,
        check_run_fn=check_run_fn,
    )

    log = get_logger()
    case_by_name = {case.name: case for case in selected_cases}
    # 若有 judge-enabled 用例参与，则 judge 标签必须存在，否则 typo 会静默移除质量信号。
    needs_judge = any(
        not record.is_error and case_by_name[record.case].judge.enabled
        for record in result.records
    )
    judge_profile = get_profile(registry, config.judge) if needs_judge else registry.get(config.judge)
    total = len(result.records)

    # deterministic check 已在原始 workdir 内完成；这里主要并发 judge。
    # score_record 仅为历史/外部 record 兼容性补跑缺失的 check。
    score_workers = min(total, 6) if total > 1 else 1
    log.info(f"[score] 判分阶段: {total} cells（judge + legacy check，workers={score_workers}）")

    def _score_one(rec: RunRecord) -> RunRecord:
        case = case_by_name[rec.case]
        jp = judge_profile if case.judge.enabled else None
        srec = score_record(
            case, rec, jp, check_run_fn=check_run_fn, judge_run_fn=judge_run_fn
        )
        out = Path(rec.artifacts_dir).parent / "run.json"
        out.write_text(srec.to_json(), encoding="utf-8")
        return srec

    scored: list[RunRecord] = []
    if score_workers <= 1:
        for i, rec in enumerate(result.records, 1):
            srec = _score_one(rec)
            scored.append(srec)
            ck = "pass" if srec.check.passed else ("fail" if srec.check.ran else "—")
            js = srec.judge.score if srec.judge and srec.judge.score is not None else "—"
            log.info(
                f"[score] {i}/{total} done · case={rec.case} runner={rec.runner_label} "
                f"variant={rec.variant_label} "
                f"check={ck} judge={js}"
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=score_workers) as ex:
            futures = {
                ex.submit(_score_one, rec): rec
                for rec in result.records
            }
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                srec = fut.result()
                scored.append(srec)
                done += 1
                orig = futures[fut]
                ck = "pass" if srec.check.passed else ("fail" if srec.check.ran else "—")
                js = srec.judge.score if srec.judge and srec.judge.score is not None else "—"
                remaining = total - done
                if remaining:
                    log.info(
                        f"[score] {done}/{total} done · "
                        f"just finished: {orig.runner_label} check={ck} judge={js} · pending={remaining}"
                    )
                else:
                    log.info(f"[score] {done}/{total} all done")

    log.info("[score] 生成计分卡…")
    locks_after = {
        case.name: snapshot_case_integrity(case) for case in selected_cases
    }
    if locks_before != locks_after:
        raise RunManifestError("case 或 protocol 文件在运行期间发生变化。")
    protected_after = {
        case_name: snapshot.protected_files
        for case_name, snapshot in locks_after.items()
    }
    integrity_verified_after = {
        case_name: snapshot.declared_files
        for case_name, snapshot in locks_after.items()
    }
    provider_runs = validate_provider_invariants(scored, case_by_name, input_hashes)
    comparison_results = compare_run_variants(scored, case_by_name)
    final = MatrixResult(
        records=scored,
        skipped=result.skipped,
        run_id=result.run_id,
        schedule=result.schedule,
    )
    md = build_scorecard(
        final,
        judge_label=config.judge,
        cases=case_by_name,
        comparisons=comparison_results,
    )
    # 计分卡就地存 runs/<run_id>/scorecard.md（一次运行自包含）；
    # scorecards/ 另存一份可浏览副本（沿用不覆盖的命名策略）。
    layout.scorecard_path.write_text(md, encoding="utf-8")
    sc_dir = report_root / "scorecards"
    sc_dir.mkdir(exist_ok=True)
    if is_private:
        path = sc_dir / f"{run_id}.md"
    else:
        filename = scorecard_filename(
            date.today().isoformat(),
            [r.case for r in scored],
            [r.runner_label for r in scored],
        )
        path = unique_scorecard_path(sc_dir, filename)
    path.write_text(md, encoding="utf-8")

    report_html = build_report_html(
        run_id=run_id,
        records=scored,
        cases=case_by_name,
        comparisons=comparison_results,
        judge_label=config.judge,
        skipped=result.skipped,
    )
    layout.report_path.write_text(report_html, encoding="utf-8")
    log.info(f"[report] HTML 报告已生成: {layout.report_path}")

    manifest.update(
        {
            "status": "complete",
            "schedule": result.schedule,
            "prompt_template_sha256": {
                f"{record.case}:{record.variant_label}": record.prompt_template_sha256
                for record in scored
            },
            "protected_after": protected_after,
            "integrity_verified_after": integrity_verified_after,
            "provider_runs": provider_runs,
            "scorecard": str(layout.scorecard_path),
            "scorecard_copy": str(path),
            "report": str(layout.report_path),
        }
    )
    _write_json(layout.manifest_path, manifest)

    if write_profiles:
        by_runner: dict[str, list] = {}
        for rec in scored:
            by_runner.setdefault(rec.runner_label, []).append(rec)
        for label, recs in by_runner.items():
            write_model_profile(root / "models", label, _profile_summary(label, recs))

    return path


def run_benchmark(
    config: RunConfig,
    registry: dict,
    root: Path,
    *,
    run_fn=run_subprocess,
    adapter_factory=get_adapter,
    judge_run_fn=run_subprocess,
    check_run_fn=_default_script_runner,
    write_profiles: bool = False,
) -> Path:
    """执行 benchmark，并将已建立 manifest 的异常终止显式标记为 failed。"""
    cases = discover_cases(root)
    run_id = generate_run_id()
    plan = plan_matrix(
        config,
        registry,
        cases,
        adapter_factory=adapter_factory,
        run_id=run_id,
    )
    is_private, report_root = _report_context(
        list(plan.selected_cases), root, write_profiles
    )
    manifest_path = RunLayout(report_root, run_id).manifest_path
    try:
        return _run_benchmark_impl(
            config,
            registry,
            root,
            plan,
            is_private,
            report_root,
            run_fn=run_fn,
            judge_run_fn=judge_run_fn,
            check_run_fn=check_run_fn,
            write_profiles=write_profiles,
        )
    except Exception as exc:
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                manifest = {
                    "schema_version": 1,
                    "run_id": run_id,
                    "private": is_private,
                }
            manifest.update(
                {
                    "status": "failed",
                    "error": {
                        "type": type(exc).__name__,
                        "message": scrub_text(str(exc)),
                    },
                }
            )
            _write_json(manifest_path, manifest)
        raise


def _candidate_report_roots(root: Path) -> list[Path]:
    """公开产物根（仓库根）+ 各私有 cases 根的上级（私有产物根）。"""
    roots = [root]
    for case_root in resolve_case_roots(root)[1:]:
        parent = case_root.parent
        if parent not in roots:
            roots.append(parent)
    return roots


def rebuild_report(run_id: str, root: Path) -> Path:
    """对已完成的 run 从磁盘重建 HTML 报告（不重跑、不重判分）。"""
    layout = find_run_layout(run_id, _candidate_report_roots(root))
    manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    records = load_run_records(layout)
    cases = {c.name: c for c in discover_cases(root)}
    referenced = {record.case for record in records}
    missing = sorted(referenced - set(cases))
    if missing:
        raise ReportError(f"run 引用的 case 已不存在，无法重建: {', '.join(missing)}")
    case_by_name = {name: cases[name] for name in referenced}
    html = build_report_html(
        run_id=run_id,
        records=records,
        cases=case_by_name,
        comparisons=compare_run_variants(records, case_by_name),
        judge_label=str(manifest.get("judge") or "—"),
    )
    layout.report_path.write_text(html, encoding="utf-8")
    return layout.report_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_log(quiet=args.quiet)
    try:
        registry = load_registry(ROOT / "runners.yaml")
    except RegistryError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    cases = discover_cases(ROOT)

    if args.list:
        print(cmd_list(registry, cases))
        return 0

    if args.report:
        try:
            report_path = rebuild_report(args.report, ROOT)
        except (ReportError, ComparisonError, CaseError, ValueError) as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        print(f"HTML 报告已生成: {report_path}")
        return 0

    try:
        base = load_config(ROOT / "config.yaml")
    except ConfigError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    try:
        config = merge_config(base, args)
        for label in config.runners:
            get_profile(registry, label)  # 提前校验标签存在
        path = run_benchmark(config, registry, ROOT, write_profiles=args.write_profiles)
    except (
        RegistryError,
        OrchestratorError,
        RunManifestError,
        CaseError,
        ConfigError,
        KeyError,
        ValueError,
    ) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(f"计分卡已生成: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
