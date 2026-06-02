"""编排器：runners × cases × repeat 矩阵执行 + 指标采集（R4/R8/R9/R10/R17/R19）。

每格：requires_engine 兼容性 → 隔离 workdir → 最小环境子进程 + 墙钟 →
解析 usage → files_changed 快照 diff → 脱敏 → run record。
单元失败不中断其它格。
"""

from __future__ import annotations

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
from .record import Agentic, RunRecord, Usage
from .registry import RunnerProfile, get_profile
from .scrub import scrub_text

DEFAULT_TIMEOUT_S = 900


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
    """执行 runners × cases × repeat 矩阵。"""
    records: list[RunRecord] = []
    skipped: list[SkippedCell] = []
    selected_cases = _select_cases(config, cases)

    for label in _select_runners(config, registry):
        profile = get_profile(registry, label)
        adapter = adapter_factory(profile)
        for case in selected_cases:
            if not case.supports_launcher(adapter.launcher_type):
                skipped.append(
                    SkippedCell(
                        case=case.name,
                        runner_label=label,
                        reason=f"requires_engine={case.requires_engine}，{adapter.launcher_type} 不兼容",
                    )
                )
                continue
            repeat = case.repeat if case.repeat is not None else config.repeat
            for i in range(max(1, repeat)):
                records.append(
                    _execute_cell(case, profile, adapter, i, run_fn, clock, now)
                )

    return MatrixResult(records=records, skipped=skipped)
