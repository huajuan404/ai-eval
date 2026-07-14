"""编排器：runners × cases × repeat 矩阵执行 + 指标采集（R4/R8/R9/R10/R17/R19）。

每格：requires_engine 兼容性 → 隔离 workdir → 最小环境子进程 + 墙钟 →
解析 usage → files_changed 快照 diff → 脱敏 → run record。
单元失败不中断其它格。

并发：runners × cases × repeat 的每一格是独立 workdir 子进程，天然线程安全。
`config.workers > 1` 时用 ThreadPoolExecutor 并发；=1 保持串行（测试与调试）。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import random
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .adapters import Adapter, get_adapter
from .adapters.command import PROMPT_FILENAME
from .case import (
    Case,
    copy_artifacts,
    count_changed,
    isolated_workdir,
    snapshot_dir,
)
from .comparison import ComparisonError, validate_comparison_contract
from .config import RunConfig
from .layout import RunLayout
from .log import get_logger
from .record import Agentic, RunRecord, Usage
from .registry import RunnerProfile, get_profile
from .run_manifest import RunManifestError, load_request_manifest, sha256_bytes, tree_manifest_sha256
from .scrub import scrub_text

# 单格墙钟上限：2 小时。长任务能跑多久本身就是模型能力的一部分，
# 上限放宽到 2h 让「持续作业型」任务跑完，而不是 15min 一刀切误判为失败。
DEFAULT_TIMEOUT_S = 7200


@dataclass(frozen=True)
class SkippedCell:
    case: str
    runner_label: str
    reason: str
    variant_label: str = "default"


@dataclass(frozen=True)
class MatrixResult:
    records: list[RunRecord] = field(default_factory=list)
    skipped: list[SkippedCell] = field(default_factory=list)
    run_id: str = "legacy"
    schedule: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class PlannedCell:
    case: Case
    profile: RunnerProfile
    adapter: Adapter
    variant_label: str
    repeat_index: int


@dataclass(frozen=True)
class RunPlan:
    run_id: str
    selected_cases: tuple[Case, ...]
    selected_runners: tuple[str, ...]
    cells: tuple[PlannedCell, ...]
    skipped: tuple[SkippedCell, ...]
    schedule: tuple[dict[str, object], ...]
    workers: int
    workers_auto: bool


class SubprocessResult(Protocol):
    stdout: str
    stderr: str
    returncode: int


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_subprocess(
    cmd: list[str], cwd: str, env: dict[str, str], timeout: int = DEFAULT_TIMEOUT_S
) -> tuple[str, str, int | None]:
    """真实子进程执行。返回 (stdout, stderr, exit_code)。超时 → 退出码 124（显式失败）。"""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        # 解码（TimeoutExpired 的 stdout/stderr 可能是 bytes）并标记失败（124），
        # 否则 exit_code=None 会被当作非错误，超时被记成正常运行。
        return (_decode(e.stdout), _decode(e.stderr) + "\n[timeout]", 124)


RunFn = Callable[[list[str], str, dict[str, str]], tuple[str, str, "int | None"]]
AdapterFactory = Callable[[RunnerProfile], Adapter]


def _select_runners(config: RunConfig, registry: dict[str, RunnerProfile]) -> list[str]:
    selected = list(config.runners) if config.runners else sorted(registry)
    if len(selected) != len(set(selected)):
        raise OrchestratorError("runner 选择含重复 label。")
    return selected


class OrchestratorError(ValueError):
    """编排错误（如指定了不存在的用例）。"""


def _select_cases(config: RunConfig, cases: list[Case]) -> list[Case]:
    names = [case.name for case in cases]
    if len(names) != len(set(names)):
        raise OrchestratorError("可用 case 含重复 name。")
    if not config.cases:
        return cases
    if len(config.cases) != len(set(config.cases)):
        raise OrchestratorError("case 选择含重复 name。")
    by_name = {c.name: c for c in cases}
    unknown = [n for n in config.cases if n not in by_name]
    if unknown:
        available = ", ".join(sorted(by_name)) or "(无)"
        raise OrchestratorError(
            f"指定的用例不存在: {', '.join(unknown)}；可用用例: {available}。"
        )
    return [by_name[n] for n in config.cases]


def select_cases(config: RunConfig, cases: list[Case]) -> list[Case]:
    """公开选择入口，供 CLI 在运行前做隐私与报告路由校验。"""
    return _select_cases(config, cases)


def _select_variants(config: RunConfig, case: Case) -> list[str]:
    requested = list(config.variants)
    if len(requested) != len(set(requested)):
        raise OrchestratorError("--variants 含重复 label。")
    if "" in requested:
        raise OrchestratorError("--variants 含空 label。")
    if "*" in requested:
        if requested != ["*"]:
            raise OrchestratorError("--variants '*' 不能与其他 label 同时使用。")
        return list(case.task.variant_labels)
    if not requested:
        return [case.task.default_variant]
    unknown = [label for label in requested if label not in case.task.variant_labels]
    if unknown:
        raise OrchestratorError(
            f"用例 '{case.name}' 不存在 prompt variant: {', '.join(unknown)}；"
            f"可用: {', '.join(case.task.variant_labels)}。"
        )
    return requested


def generate_run_id(now: Callable[[], str] | None = None) -> str:
    timestamp = (now() if now else datetime.now(timezone.utc).isoformat()).replace(":", "")
    compact = timestamp.replace("-", "").replace("+0000", "Z").split(".", 1)[0]
    return f"{compact}-{secrets.token_hex(4)}"


def _variant_schedule(labels: list[str], repeat: int, seed_text: str) -> list[list[str]]:
    base = list(labels)
    rng = random.Random(int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest(), 16))
    rng.shuffle(base)
    schedule: list[list[str]] = []
    for index in range(repeat):
        if len(base) == 2:
            schedule.append(base if index % 2 == 0 else list(reversed(base)))
        elif len(base) > 2:
            offset = index % len(base)
            schedule.append(base[offset:] + base[:offset])
        else:
            schedule.append(list(base))
    return schedule


def _execute_cell(
    case: Case,
    profile: RunnerProfile,
    adapter: Adapter,
    layout: RunLayout,
    variant_label: str,
    repeat_index: int,
    run_fn: RunFn,
    clock: Callable[[], float],
    now: Callable[[], str],
) -> RunRecord:
    run_id = layout.run_id
    cell_dir = layout.cell_dir(case.name, variant_label, profile.label, repeat_index)
    cell_dir.mkdir(parents=True, exist_ok=False)
    variant = case.task.variant_for(variant_label)
    prompt = variant.prompt
    prompt_sha256 = sha256_bytes(prompt.encode("utf-8"))
    input_manifest_sha256, input_manifest = tree_manifest_sha256(case.input_dir)
    base = dict(
        case=case.name,
        runner_label=profile.label,
        launcher_type=adapter.launcher_type,
        runner_model=profile.model or "",
        run_id=run_id,
        variant_label=variant_label,
        prompt_template_sha256=prompt_sha256,
        input_manifest_sha256=input_manifest_sha256,
        repeat_index=repeat_index,
        started_at=now(),
    )
    with isolated_workdir(case) as wd:
        # command 适配器约定从 workdir/PROMPT.txt 读 prompt
        (wd / PROMPT_FILENAME).write_text(prompt, encoding="utf-8")
        (wd / "RUN_CONTEXT.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "case": case.name,
                    "protocol": case.protocol.name if case.protocol else None,
                    "variant": {
                        "label": variant.label,
                        "parameters": variant.parameters,
                    },
                    "repeat_index": repeat_index,
                    "prompt_template_sha256": prompt_sha256,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (wd / "input-manifest.json").write_text(
            json.dumps(input_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        cmd = adapter.build_command(profile, prompt, str(wd))
        env = adapter.build_env(profile)
        before = snapshot_dir(wd)
        t0 = clock()
        try:
            stdout, stderr, exit_code = run_fn(cmd, str(wd), env)
            failed = False
        except Exception as exc:  # 启动器缺失/崩溃：本格失败，不中断矩阵
            stdout, stderr, exit_code = "", f"{type(exc).__name__}: {exc}", None
            failed = True
        duration_ms = int((clock() - t0) * 1000)
        artifact_error: Exception | None = None
        try:
            after = snapshot_dir(wd)
            files_changed = count_changed(before, after)
        except Exception as exc:
            artifact_error = exc
            files_changed = 0
            stderr = (stderr + "\n" if stderr else "") + f"{type(exc).__name__}: {exc}"

        usage: Usage | None = None
        num_turns: int | None = None
        is_error = failed or artifact_error is not None or (exit_code not in (0, None))
        if not failed:
            parsed = adapter.parse(stdout, stderr, exit_code)
            num_turns = parsed.num_turns
            is_error = parsed.is_error
            if profile.metrics != "none" and adapter.supports_usage:
                usage = parsed.usage

        request_manifest = case.run_contract.request_manifest_file
        if request_manifest:
            request_path = wd / request_manifest
            try:
                if request_path.is_file():
                    load_request_manifest(request_path)
                elif case.run_contract.request_manifest_required:
                    raise RunManifestError(f"request manifest 缺失: {request_manifest}")
            except RunManifestError as exc:
                is_error = True
                stderr = (stderr + "\n" if stderr else "") + str(exc)

        artifacts_dir = cell_dir / "artifacts"
        try:
            copy_artifacts(wd, artifacts_dir)
        except Exception as exc:
            is_error = True
            stderr = (stderr + "\n" if stderr else "") + f"{type(exc).__name__}: {exc}"
        # 写出模型最终回答文本，供 judge 评分（纯分析任务的"产物"是回答而非文件）。
        # OUTPUT.txt 是 check.sh / judge 读的结构化答案，**不脱敏**（避免 40 字符 hex 等
        # 误伤 commit hash / 答案 hash）。raw.txt 仍是脱敏的（给人类看 / 分享用）。
        final_text = adapter.extract_final_text(stdout) if not failed else ""
        (artifacts_dir / "OUTPUT.txt").write_text(final_text, encoding="utf-8")
        (cell_dir / "raw.txt").write_text(
            scrub_text(stdout) + ("\n--- stderr ---\n" + scrub_text(stderr) if stderr else ""),
            encoding="utf-8",
        )

    record = RunRecord(
        **base,
        duration_ms=duration_ms,
        exit_code=exit_code,
        is_error=is_error,
        usage=usage,
        agentic=Agentic(num_turns=num_turns, files_changed=files_changed),
        artifacts_dir=str(artifacts_dir),
    )
    (cell_dir / "run.json").write_text(record.to_json(), encoding="utf-8")
    return record


def _run_one(
    case: Case,
    profile: RunnerProfile,
    adapter: Adapter,
    layout: RunLayout,
    variant_label: str,
    repeat_index: int,
    run_fn: RunFn,
    clock: Callable[[], float],
    now: Callable[[], str],
    log: logging.Logger,
) -> RunRecord:
    """单格执行 + 节点日志（start / done）。"""
    log.info(
        f"[start] case={case.name} runner={profile.label} variant={variant_label} repeat={repeat_index}"
    )
    rec = _execute_cell(
        case, profile, adapter, layout, variant_label, repeat_index, run_fn, clock, now
    )
    status = "err" if rec.is_error else "ok"
    bits = [f"status={status}", f"dur={rec.duration_ms}ms"]
    if rec.usage and rec.usage.total_tokens is not None:
        bits.append(f"tokens={rec.usage.total_tokens}")
        if rec.usage.cost_usd is not None:
            bits.append(f"cost=${rec.usage.cost_usd:.4f}")
    else:
        bits.append("tokens=—")
    log.info(
        f"[done ] case={case.name} runner={profile.label} variant={variant_label} repeat={repeat_index} "
        + " ".join(bits)
    )
    return rec


def _run_group(
    cells: tuple[PlannedCell, ...],
    layout: RunLayout,
    run_fn: RunFn,
    clock: Callable[[], float],
    now: Callable[[], str],
    log: logging.Logger,
) -> list[RunRecord]:
    """Run one case/runner schedule serially so variant order is real."""
    return [
        _run_one(
            cell.case,
            cell.profile,
            cell.adapter,
            layout,
            cell.variant_label,
            cell.repeat_index,
            run_fn,
            clock,
            now,
            log,
        )
        for cell in cells
    ]


def plan_matrix(
    config: RunConfig,
    registry: dict[str, RunnerProfile],
    cases: list[Case],
    *,
    adapter_factory: AdapterFactory = get_adapter,
    run_id: str | None = None,
) -> RunPlan:
    """Resolve and validate the complete matrix before execution side effects."""
    skipped: list[SkippedCell] = []
    selected_cases = _select_cases(config, cases)
    selected_runners = _select_runners(config, registry)
    for case in selected_cases:
        try:
            validate_comparison_contract(case)
        except ComparisonError as exc:
            raise OrchestratorError(str(exc)) from exc
    current_run_id = run_id or generate_run_id()
    schedule_manifest: list[dict[str, object]] = []
    variants_by_case = {
        case.name: _select_variants(config, case) for case in selected_cases
    }
    if config.workers == 0:
        workers = min(len(selected_runners), 6) if selected_runners else 1
    else:
        workers = config.workers

    cells: list[PlannedCell] = []
    for label in selected_runners:
        profile = get_profile(registry, label)
        if profile.label != label:
            raise OrchestratorError(
                f"runner registry key/label 不一致: key={label} label={profile.label}"
            )
        adapter = adapter_factory(profile)
        for case in selected_cases:
            variants = variants_by_case[case.name]
            if not case.supports_launcher(adapter.launcher_type):
                reason = (
                    f"requires_engine={case.requires_engine}，"
                    f"{adapter.launcher_type} 不兼容"
                )
                skipped.extend(
                    SkippedCell(
                        case=case.name,
                        runner_label=label,
                        variant_label=variant,
                        reason=reason,
                    )
                    for variant in variants
                )
                continue
            repeat = case.repeat if case.repeat is not None else config.repeat
            schedule = _variant_schedule(
                variants, repeat, f"{current_run_id}:{case.name}:{label}"
            )
            for i, ordered_variants in enumerate(schedule):
                schedule_manifest.append(
                    {
                        "case": case.name,
                        "runner": label,
                        "repeat_index": i,
                        "variants": list(ordered_variants),
                    }
                )
                for variant_label in ordered_variants:
                    cells.append(
                        PlannedCell(
                            case=case,
                            profile=profile,
                            adapter=adapter,
                            variant_label=variant_label,
                            repeat_index=i,
                        )
                    )

    identities = [
        (
            cell.case.name,
            cell.profile.label,
            cell.variant_label,
            cell.repeat_index,
        )
        for cell in cells
    ]
    if len(identities) != len(set(identities)):
        raise OrchestratorError("RunPlan 含重复 cell identity。")
    if not cells:
        reasons = "; ".join(
            f"{item.case}/{item.runner_label}: {item.reason}" for item in skipped
        )
        detail = f" 原因: {reasons}。" if reasons else ""
        raise OrchestratorError(
            "所选 case 与 runner 没有可执行组合，不生成空计分卡。"
            f"{detail}请先运行 ./run.sh -l，并通过 -r <runner名> 选择兼容 runner。"
        )

    return RunPlan(
        run_id=current_run_id,
        selected_cases=tuple(selected_cases),
        selected_runners=tuple(selected_runners),
        cells=tuple(cells),
        skipped=tuple(skipped),
        schedule=tuple(schedule_manifest),
        workers=workers,
        workers_auto=config.workers == 0,
    )


def execute_plan(
    plan: RunPlan,
    layout: RunLayout,
    *,
    run_fn: RunFn = run_subprocess,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
) -> MatrixResult:
    """Execute an immutable, fully validated run plan; all artifacts land in layout.run_dir."""
    if layout.run_id != plan.run_id:
        raise OrchestratorError(
            f"layout.run_id 与 plan.run_id 不一致: {layout.run_id} != {plan.run_id}"
        )
    log = get_logger()
    records: list[RunRecord] = []
    total_cells = len(plan.cells) + len(plan.skipped)
    workers_tag = "auto" if plan.workers_auto else str(plan.workers)
    log.info(
        f"[bench] 矩阵启动: {len(plan.selected_runners)} runners × "
        f"{len(plan.selected_cases)} cases = {total_cells} cells "
        f"(workers={workers_tag} → {plan.workers})"
    )
    for skipped in plan.skipped:
        log.info(
            f"[skip ] case={skipped.case} runner={skipped.runner_label} "
            f"variant={skipped.variant_label} reason={skipped.reason}"
        )

    if not plan.cells:
        return MatrixResult(
            records=records,
            skipped=list(plan.skipped),
            run_id=plan.run_id,
            schedule=list(plan.schedule),
        )

    if plan.workers <= 1:
        for cell in plan.cells:
            records.append(
                _run_one(
                    cell.case,
                    cell.profile,
                    cell.adapter,
                    layout,
                    cell.variant_label,
                    cell.repeat_index,
                    run_fn,
                    clock,
                    now,
                    log,
                )
            )
    else:
        grouped: dict[tuple[str, str], list[PlannedCell]] = {}
        for cell in plan.cells:
            grouped.setdefault((cell.case.name, cell.profile.label), []).append(cell)
        with concurrent.futures.ThreadPoolExecutor(max_workers=plan.workers) as ex:
            futures = {
                ex.submit(
                    _run_group,
                    tuple(cells),
                    layout,
                    run_fn,
                    clock,
                    now,
                    log,
                ): key
                for key, cells in grouped.items()
            }
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                group_records = fut.result()
                records.extend(group_records)
                done += len(group_records)
                _, just_finished = futures[fut]
                remaining = len(plan.cells) - done
                if remaining:
                    log.info(
                        f"[progress] {done}/{len(plan.cells)} done · "
                        f"just finished: {just_finished} · pending={remaining}"
                    )
                else:
                    log.info(f"[progress] {done}/{len(plan.cells)} all done")

        by_cell = {
            (
                record.case,
                record.runner_label,
                record.variant_label,
                record.repeat_index,
            ): record
            for record in records
        }
        records = [
            by_cell[
                (
                    cell.case.name,
                    cell.profile.label,
                    cell.variant_label,
                    cell.repeat_index,
                )
            ]
            for cell in plan.cells
        ]

    log.info(
        f"[bench] 矩阵完成: {len(records)}/{total_cells} 完成，跳过 {len(plan.skipped)}"
    )
    return MatrixResult(
        records=records,
        skipped=list(plan.skipped),
        run_id=plan.run_id,
        schedule=list(plan.schedule),
    )


def run_matrix(
    config: RunConfig,
    registry: dict[str, RunnerProfile],
    cases: list[Case],
    *,
    report_root: "str | Path",
    run_fn: RunFn = run_subprocess,
    adapter_factory: AdapterFactory = get_adapter,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    run_id: str | None = None,
    schedule_callback: Callable[[list[dict[str, object]]], None] | None = None,
) -> MatrixResult:
    """Compatibility wrapper: resolve a plan, persist it, then execute it."""
    plan = plan_matrix(
        config,
        registry,
        cases,
        adapter_factory=adapter_factory,
        run_id=run_id,
    )
    if schedule_callback is not None:
        schedule_callback(list(plan.schedule))
    layout = RunLayout(Path(report_root), plan.run_id)
    return execute_plan(plan, layout, run_fn=run_fn, clock=clock, now=now)
