"""session-to-eval U6：落盘校验门。

路径信任校验 → `bench.case.load_case` 静态加载 → 廉价确定性断言。

**明确边界**：本校验只证明 case 结构合法、能被 bench 加载，并补几条廉价断言；
**不**执行 check.sh、**不**跑评测，因此**不证明 case 能跑出有意义的分**（那是后续的可选 dry-run）。
区分两个状态：valid（能加载 + 无结构错误）与 complete（无草稿 TODO，真值已补）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from leak_check import check_leak
from session_extract import ConfigError

# 真值桩约定含 "TODO"（见 SKILL.md 生成模板）。不含 "<"：真实 rubric 常含 `<n>` JSON 模板，
# 会误判为占位。
_PLACEHOLDER_MARKERS = ("TODO", "FIXME", "占位", "placeholder")


@dataclass(frozen=True)
class ValidationResult:
    valid: bool                  # 结构合法、能 load_case、无 errors
    complete: bool               # valid 且无草稿 TODO（真值/桩已补）
    errors: tuple[str, ...]
    todos: tuple[str, ...]       # 待补项：阻止 complete，但不阻止 valid
    warnings: tuple[str, ...]


def validate_ai_eval_path(ai_eval_path: str | Path) -> Path:
    """信任边界：加入 sys.path 前，确认是真实目录且含 bench/__init__.py 与 bench/case.py。"""
    p = Path(ai_eval_path).resolve()
    if not p.is_dir():
        raise ConfigError(f"ai_eval_path 不是目录：{p}")
    if not (p / "bench" / "__init__.py").exists() or not (p / "bench" / "case.py").exists():
        raise ConfigError(f"ai_eval_path 不含 bench/__init__.py 与 bench/case.py，疑似配错：{p}")
    return p


def _looks_placeholder(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    return any(m in t for m in _PLACEHOLDER_MARKERS)


def _has_files(d: Path) -> bool:
    return d.exists() and any(f.is_file() for f in d.rglob("*"))


def _has_real_ground_truth(case) -> bool:
    """expected 去掉 skill 自加的 synthesized 标记后，是否有非占位真值。"""
    real = {k: v for k, v in (case.expected or {}).items() if k != "synthesized"}
    for v in real.values():
        if isinstance(v, str) and _looks_placeholder(v):
            continue
        if v in (None, "", []):
            continue
        return True
    return False


def validate_case(case_dir: str | Path, ai_eval_path: str | Path) -> ValidationResult:
    """对生成的 case 做静态结构校验 + 廉价断言。"""
    root = validate_ai_eval_path(ai_eval_path)  # 信任边界，不合格直接抛
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from bench.case import load_case

    cd = Path(case_dir)
    errors: list[str] = []
    todos: list[str] = []
    warnings: list[str] = []

    try:
        case = load_case(cd)
    except Exception as e:  # CaseError 等：结构不合法
        return ValidationResult(False, False, (f"load_case 失败：{type(e).__name__}: {e}",), (), ())

    if case.task.type in ("prompt", "custom") and not case.task.prompt.strip():
        errors.append("task prompt 为空")

    if case.judge.enabled and _looks_placeholder(case.judge.rubric):
        errors.append("judge 已启用但 rubric 为空或像占位")

    if case.check.type == "script":
        script = cd / (case.check.script or "")
        if not script.exists():
            errors.append(f"check.script 不存在：{case.check.script}")

    # coding 的 ground-truth 是 verify/ 测试；tool-using 的是 expected 真值。
    if case.class_ == "coding" and not _has_files(case.verify_dir):
        todos.append("coding ground-truth 待补：verify/ 缺只读测试基准")
    if case.class_ == "tool-using" and not _has_real_ground_truth(case):
        todos.append("tool-using ground-truth 待补：expected 真值仍是桩，请人工填权威值（勿用 agent 自身输出）")

    # advisory：答案泄漏（污染）检查——不阻断 valid，但强烈提示区分度无效
    leak = check_leak(cd)
    for f in leak.findings:
        warnings.append(f"疑似答案泄漏[{f.kind}]：{f.detail}")

    # advisory：judge-only 用例缺完成判据 → 任务完成度算不出来
    expected = case.expected or {}
    has_completion_criterion = (
        expected.get("passing_threshold") is not None
        or (expected.get("completion") or {}).get("core_dimensions")
    )
    if case.judge.enabled and case.check.type != "script" and not has_completion_criterion:
        warnings.append(
            "judge-only 用例缺 expected.passing_threshold（或 completion.core_dimensions）："
            "任务完成度无判据，计分卡该格将显示「未评」"
        )

    valid = not errors
    complete = valid and not todos
    return ValidationResult(valid, complete, tuple(errors), tuple(todos), tuple(warnings))


def _main(argv=None) -> int:
    import argparse

    from session_extract import load_config

    ap = argparse.ArgumentParser(description="校验生成的 case（静态结构 + 廉价断言）。")
    ap.add_argument("case_dir")
    ap.add_argument("--ai-eval-path", default=None, help="覆盖 config.toml 的 ai_eval_path")
    args = ap.parse_args(argv)
    aep = args.ai_eval_path or load_config()["ai_eval_path"]
    res = validate_case(args.case_dir, aep)
    print(f"valid={res.valid} complete={res.complete}")
    for e in res.errors:
        print(f"  [error] {e}")
    for t in res.todos:
        print(f"  [todo]  {t}")
    for w in res.warnings:
        print(f"  [warn]  {w}")
    return 0 if res.valid else 1


if __name__ == "__main__":
    raise SystemExit(_main())
