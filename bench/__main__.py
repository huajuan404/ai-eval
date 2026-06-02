"""bench CLI 入口（R15/R16，KTD1）。

  python3 -m bench            # 按 config.yaml 跑全部
  python3 -m bench -l         # 列出可用用例与 runner 档案
  python3 -m bench -c <case> -r codex,claude   # 指定用例与 runner（覆盖 config）
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .adapters import get_adapter
from .case import list_cases
from .config import ConfigError, RunConfig, load_config
from .orchestrator import MatrixResult, OrchestratorError, run_matrix, run_subprocess
from .registry import RegistryError, get_profile, load_registry
from .scorecard import build_scorecard, write_model_profile
from .scoring import _default_script_runner, score_record

ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description="可换模型的端到端 agent benchmark")
    p.add_argument("-c", "--case", action="append", default=[], help="只跑指定用例（可多次）")
    p.add_argument("-r", "--runners", default=None, help="逗号分隔的 runner 标签，覆盖 config")
    p.add_argument("-j", "--judge", default=None, help="裁判档案标签，覆盖 config")
    p.add_argument("--repeat", type=int, default=None, help="每格运行次数，覆盖 config")
    p.add_argument("-l", "--list", action="store_true", help="列出可用用例与 runner 档案")
    p.add_argument(
        "--write-profiles",
        action="store_true",
        help="把本次各 runner 的表现写入 models/<label>.md",
    )
    return p


def merge_config(base: RunConfig, args: argparse.Namespace) -> RunConfig:
    """CLI 覆盖 config：提供的字段覆盖，未提供则沿用 config。"""
    return RunConfig(
        runners=tuple(s.strip() for s in args.runners.split(",")) if args.runners else base.runners,
        cases=tuple(args.case) if args.case else base.cases,
        judge=args.judge or base.judge,
        repeat=args.repeat if args.repeat is not None else base.repeat,
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
            lines.append(f"  {c.name}  ({c.task.type}){tag}")
    else:
        lines.append("  (无；在 cases/ 下创建带 case.yaml 的用例)")
    return "\n".join(lines)


def _profile_summary(label: str, records: list) -> str:
    """为某 runner 生成一行档案摘要。"""
    bits = []
    for rec in records:
        check = "pass" if rec.check.passed else ("fail" if rec.check.ran else "—")
        jscore = rec.judge.score if rec.judge and rec.judge.score is not None else "—"
        bits.append(f"{rec.case}: check={check}, judge={jscore}, {rec.duration_ms}ms")
    return "；".join(bits)


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
    """执行矩阵 → 判分 → 计分卡，返回计分卡路径。"""
    cases = list_cases(root / "cases")
    result = run_matrix(
        config, registry, cases, run_fn=run_fn, adapter_factory=adapter_factory
    )  # 未知用例会 fail-fast

    case_by_name = {c.name: c for c in cases}
    # 若有 judge-enabled 用例参与，则 judge 标签必须存在，否则 typo 会静默移除质量信号。
    needs_judge = any(case_by_name[r.case].judge.enabled for r in result.records)
    judge_profile = get_profile(registry, config.judge) if needs_judge else registry.get(config.judge)
    scored = []
    for rec in result.records:
        case = case_by_name[rec.case]
        jp = judge_profile if case.judge.enabled else None
        srec = score_record(
            case, rec, jp, check_run_fn=check_run_fn, judge_run_fn=judge_run_fn
        )
        out = case.output_dir(rec.runner_label) / f"run.{rec.repeat_index}.json"
        out.write_text(srec.to_json(), encoding="utf-8")
        scored.append(srec)

    final = MatrixResult(records=scored, skipped=result.skipped)
    md = build_scorecard(final, judge_label=config.judge)
    sc_dir = root / "scorecards"
    sc_dir.mkdir(exist_ok=True)
    path = sc_dir / f"{date.today().isoformat()}.md"
    path.write_text(md, encoding="utf-8")

    if write_profiles:
        by_runner: dict[str, list] = {}
        for rec in scored:
            by_runner.setdefault(rec.runner_label, []).append(rec)
        for label, recs in by_runner.items():
            write_model_profile(root / "models", label, _profile_summary(label, recs))

    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_registry(ROOT / "runners.yaml")
    except RegistryError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    cases = list_cases(ROOT / "cases")

    if args.list:
        print(cmd_list(registry, cases))
        return 0

    try:
        base = load_config(ROOT / "config.yaml")
    except ConfigError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    config = merge_config(base, args)

    try:
        for label in config.runners:
            get_profile(registry, label)  # 提前校验标签存在
        path = run_benchmark(config, registry, ROOT, write_profiles=args.write_profiles)
    except (RegistryError, OrchestratorError, KeyError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(f"计分卡已生成: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
