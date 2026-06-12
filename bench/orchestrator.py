"""编排器：runners × cases × repeat 矩阵执行 + 指标采集（R4/R8/R9/R10/R17/R19）。

每格：requires_engine 兼容性 → 隔离 workdir → 最小环境子进程 + 墙钟 →
解析 usage → files_changed 快照 diff → 脱敏 → run record。
单元失败不中断其它格。

并发：runners × cases × repeat 的每一格是独立 workdir 子进程，天然线程安全。
`config.workers > 1` 时用 ThreadPoolExecutor 并发；=1 保持串行（测试与调试）。
"""

from __future__ import annotations

import concurrent.futures
import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from .config import RunConfig
from .log import get_logger
from .record import Agentic, RunRecord, Usage
from .registry import RunnerProfile, get_profile
from .scrub import scrub_text

# 单格墙钟上限：2 小时。长任务能跑多久本身就是模型能力的一部分，
# 上限放宽到 2h 让「持续作业型」任务跑完，而不是 15min 一刀切误判为失败。
DEFAULT_TIMEOUT_S = 7200


@dataclass(frozen=True)
class SkippedCell:
    case: str
    runner_label: str
    reason: str


@dataclass(frozen=True)
class MatrixResult:
    records: list[RunRecord] = field(default_factory=list)
    skipped: list[SkippedCell] = field(default_factory=list)


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
    return list(config.runners) if config.runners else sorted(registry)


class OrchestratorError(ValueError):
    """编排错误（如指定了不存在的用例）。"""


def _select_cases(config: RunConfig, cases: list[Case]) -> list[Case]:
    if not config.cases:
        return cases
    by_name = {c.name: c for c in cases}
    unknown = [n for n in config.cases if n not in by_name]
    if unknown:
        available = ", ".join(sorted(by_name)) or "(无)"
        raise OrchestratorError(
            f"指定的用例不存在: {', '.join(unknown)}；可用用例: {available}。"
        )
    return [by_name[n] for n in config.cases]


def _execute_cell(
    case: Case,
    profile: RunnerProfile,
    adapter: Adapter,
    repeat_index: int,
    run_fn: RunFn,
    clock: Callable[[], float],
    now: Callable[[], str],
) -> RunRecord:
    out_dir = case.output_dir(profile.label)
    base = dict(
        case=case.name,
        runner_label=profile.label,
        launcher_type=adapter.launcher_type,
        runner_model=profile.model or "",
        repeat_index=repeat_index,
        started_at=now(),
    )
    with isolated_workdir(case) as wd:
        # command 适配器约定从 workdir/PROMPT.txt 读 prompt
        (wd / PROMPT_FILENAME).write_text(case.task.prompt, encoding="utf-8")
        cmd = adapter.build_command(profile, case.task.prompt, str(wd))
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
        after = snapshot_dir(wd)
        files_changed = count_changed(before, after)

        usage: Usage | None = None
        num_turns: int | None = None
        is_error = failed or (exit_code not in (0, None))
        if not failed:
            parsed = adapter.parse(stdout, stderr, exit_code)
            num_turns = parsed.num_turns
            is_error = parsed.is_error
            if profile.metrics != "none" and adapter.supports_usage:
                usage = parsed.usage

        out_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = out_dir / f"artifacts-{repeat_index}"
        copy_artifacts(wd, artifacts_dir)
        # 写出模型最终回答文本，供 judge 评分（纯分析任务的"产物"是回答而非文件）。
        # OUTPUT.txt 是 check.sh / judge 读的结构化答案，**不脱敏**（避免 40 字符 hex 等
        # 误伤 commit hash / 答案 hash）。raw.txt 仍是脱敏的（给人类看 / 分享用）。
        final_text = adapter.extract_final_text(stdout) if not failed else ""
        (artifacts_dir / "OUTPUT.txt").write_text(final_text, encoding="utf-8")
        (out_dir / f"run.{repeat_index}.raw.txt").write_text(
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
    (out_dir / f"run.{repeat_index}.json").write_text(record.to_json(), encoding="utf-8")
    return record


def _run_one(
    case: Case,
    profile: RunnerProfile,
    adapter: Adapter,
    repeat_index: int,
    run_fn: RunFn,
    clock: Callable[[], float],
    now: Callable[[], str],
    log: logging.Logger,
) -> RunRecord:
    """单格执行 + 节点日志（start / done）。"""
    log.info(
        f"[start] case={case.name} runner={profile.label} repeat={repeat_index}"
    )
    rec = _execute_cell(case, profile, adapter, repeat_index, run_fn, clock, now)
    status = "err" if rec.is_error else "ok"
    bits = [f"status={status}", f"dur={rec.duration_ms}ms"]
    if rec.usage and rec.usage.total_tokens is not None:
        bits.append(f"tokens={rec.usage.total_tokens}")
        if rec.usage.cost_usd is not None:
            bits.append(f"cost=${rec.usage.cost_usd:.4f}")
    else:
        bits.append("tokens=—")
    log.info(
        f"[done ] case={case.name} runner={profile.label} repeat={repeat_index} "
        + " ".join(bits)
    )
    return rec


def run_matrix(
    config: RunConfig,
    registry: dict[str, RunnerProfile],
    cases: list[Case],
    *,
    run_fn: RunFn = run_subprocess,
    adapter_factory: AdapterFactory = get_adapter,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
) -> MatrixResult:
    """执行 runners × cases × repeat 矩阵。

    并发：每格独立 workdir + 独立子进程，天然线程安全。
    workers=0（自动）→ min(选中 runner 数, 6)；>1 → ThreadPoolExecutor；
    =1 → 串行（保测试确定性、便于 grep 顺序）。
    """
    log = get_logger()
    records: list[RunRecord] = []
    skipped: list[SkippedCell] = []
    selected_cases = _select_cases(config, cases)
    selected_runners = _select_runners(config, registry)

    # 解析 workers：自动 = min(provider 数, 6)，显式 1=串行，显式 N=并发 N
    if config.workers == 0:
        workers = min(len(selected_runners), 6) if selected_runners else 1
    else:
        workers = config.workers

    # 第一步：兼容性检查，先把所有要执行的 (case, profile, adapter, repeat_index) 摊平
    pending: list[tuple[Case, RunnerProfile, Adapter, int]] = []
    for label in selected_runners:
        profile = get_profile(registry, label)
        adapter = adapter_factory(profile)
        for case in selected_cases:
            if not case.supports_launcher(adapter.launcher_type):
                reason = (
                    f"requires_engine={case.requires_engine}，"
                    f"{adapter.launcher_type} 不兼容"
                )
                skipped.append(
                    SkippedCell(case=case.name, runner_label=label, reason=reason)
                )
                log.info(f"[skip ] case={case.name} runner={label} reason={reason}")
                continue
            repeat = case.repeat if case.repeat is not None else config.repeat
            for i in range(max(1, repeat)):
                pending.append((case, profile, adapter, i))

    total_cells = len(pending) + len(skipped)
    workers_tag = "auto" if config.workers == 0 else str(workers)
    log.info(
        f"[bench] 矩阵启动: {len(selected_runners)} runners × "
        f"{len(selected_cases)} cases × repeat={config.repeat} = {total_cells} cells "
        f"(workers={workers_tag} → {workers})"
    )
    if skipped:
        log.info(f"[bench] 跳过 {len(skipped)} 格（requires_engine 不兼容）")

    if not pending:
        return MatrixResult(records=records, skipped=skipped)

    # 第二步：执行（串行 or 并发）
    if workers <= 1:
        for case, profile, adapter, ri in pending:
            records.append(_run_one(case, profile, adapter, ri, run_fn, clock, now, log))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(
                    _run_one, case, profile, adapter, ri, run_fn, clock, now, log
                ): (case.name, profile.label, ri)
                for case, profile, adapter, ri in pending
            }
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                records.append(fut.result())
                done += 1
                _, just_finished, _ = futures[fut]
                remaining = len(pending) - done
                if remaining:
                    log.info(
                        f"[progress] {done}/{len(pending)} done · "
                        f"just finished: {just_finished} · pending={remaining}"
                    )
                else:
                    log.info(f"[progress] {done}/{len(pending)} all done")

    log.info(
        f"[bench] 矩阵完成: {len(records)}/{total_cells} 完成，跳过 {len(skipped)}"
    )
    return MatrixResult(records=records, skipped=skipped)
