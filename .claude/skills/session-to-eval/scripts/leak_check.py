"""静态泄漏检查：模型该自己推出的"答案"是否已经写进 `input/` 或 `task.md`（污染检测）。

**为什么必须是静态检查、不能是 runner**：它要读 `case.yaml` 的 `expected`（oracle）才能知道"答案是什么"，
而 runner 按设计永远看不到 `expected`。所以"答案∈输入"这件事，只有落盘期/校验门能精确判。0 token。

判据（低误报优先）：
1. **input 结论字段**：`input/*.json` 里命中结论类字段名（problem_analysis/root_cause/根因…）且**值非空** —— 最强信号。
2. **task.md 结论标签**：task.md 正文里出现结论类字段名当小节标题（如"问题描述（problem_analysis）"）。
3. **长答案回显（advisory）**：`expected` 里承载判定的**整句**真值（≥8 字，跳过 ID/数字/短标签）出现在 `input/` 事实里。

刻意**不**对"用户侧/非问题/P2"这类短分类标签做回显匹配——它们在业务规则/输出 schema 里合法出现，匹配必误报。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# input JSON 字段名层面：命中即可疑（含中文键，因 JSON 键直接命名结论=强信号）
_CONCLUSION_FIELD_RE = re.compile(
    r"(problem_analysis|root_cause|diagnosis|verdict|judgement|judgment|"
    r"根因|判定结果|问题分析|分析结论|定级结论)",
    re.IGNORECASE,
)
# task.md 正文层面：只认 snake_case/英文字段 token——这类出现在散文里几乎必是照搬的字段标签（泄漏）；
# 中文常用词（"分析""根因"）在正常说明里也会出现，纳入会误报，故 task 检查不用中文词。
_CONCLUSION_TOKEN_RE = re.compile(
    r"\b(problem_analysis|root_cause|diagnosis|verdict|judgement|judgment)\b", re.IGNORECASE
)
# expected 里承载"判定真值"的键：递归进它下面收集答案值
_JUDGMENT_KEYS = ("expected", "answer", "label", "judgment", "truth", "ground_truth", "verdict")
# 收集到的答案值里，过滤无判别力的（ID / 数字 / N/A / 占位）
_SKIP_VALUE_RE = re.compile(r"^(TKT-|TODO|FIXME)|^n/?a$|^\d+(\.\d+)?$", re.IGNORECASE)
_MIN_ECHO_LEN = 8  # 只对"整句"答案做回显匹配，短标签会在 schema/规则里合法出现


@dataclass(frozen=True)
class LeakFinding:
    kind: str  # conclusion-field-input | conclusion-label-task | answer-echo-input
    detail: str


@dataclass(frozen=True)
class LeakReport:
    leaked: bool
    findings: tuple[LeakFinding, ...]

    def summary(self) -> str:
        if not self.leaked:
            return "无泄漏：input/task 未发现答案。"
        lines = [f"⚠️ 疑似答案泄漏（{len(self.findings)} 处）——模型可直接照抄，区分度无效："]
        lines += [f"  [{f.kind}] {f.detail}" for f in self.findings]
        return "\n".join(lines)


def _collect_answer_values(expected) -> set[str]:
    """递归 expected：进入 _JUDGMENT_KEYS 命名的 dict 后，收集其字符串叶值（答案）。"""
    out: set[str] = set()

    def rec(node, under_judgment: bool) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                rec(v, under_judgment or str(k).lower() in _JUDGMENT_KEYS)
        elif isinstance(node, list):
            for v in node:
                rec(v, under_judgment)
        elif under_judgment and isinstance(node, str):
            s = node.strip()
            if s and not _SKIP_VALUE_RE.match(s):
                out.add(s)

    rec(expected, False)
    return out


def _conclusion_fields_in_input(input_dir: Path) -> list[str]:
    hits: list[str] = []
    if not input_dir.exists():
        return hits
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.suffix != ".json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        def rec(node, path: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if _CONCLUSION_FIELD_RE.search(str(k)) and isinstance(v, str) and v.strip():
                        hits.append(f"{p.name}:{path}{k} 非空结论字段（{v[:30]}…）")
                    rec(v, f"{path}{k}.")
            elif isinstance(node, list):
                for v in node:
                    rec(v, path)

        rec(data, "")
    return hits


def _read_text_files(input_dir: Path) -> str:
    parts: list[str] = []
    if input_dir.exists():
        for p in sorted(input_dir.rglob("*")):
            if p.is_file():
                try:
                    parts.append(p.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
    return "\n".join(parts)


def check_leak(case_dir: str | Path) -> LeakReport:
    cd = Path(case_dir)
    try:
        cy = yaml.safe_load((cd / "case.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cy = {}
    expected = cy.get("expected") or {}
    input_dir = cd / "input"
    input_text = _read_text_files(input_dir)
    task_md = ""
    tp = cd / "prompts" / "task.md"
    if tp.exists():
        task_md = tp.read_text(encoding="utf-8", errors="replace")

    findings: list[LeakFinding] = []
    # 1. input 结论字段非空
    for h in _conclusion_fields_in_input(input_dir):
        findings.append(LeakFinding("conclusion-field-input", h))
    # 2. task.md 出现 snake_case 结论字段 token（照搬字段标签 = 泄漏）
    for m in dict.fromkeys(_CONCLUSION_TOKEN_RE.findall(task_md)):
        findings.append(LeakFinding("conclusion-label-task", f"task.md 出现结论字段标签「{m}」"))
    # 3. 长答案整句回显进 input（advisory）
    for v in _collect_answer_values(expected):
        if len(v) >= _MIN_ECHO_LEN and v in input_text:
            findings.append(LeakFinding("answer-echo-input", f"答案整句「{v[:30]}…」出现在 input/"))
    return LeakReport(bool(findings), tuple(findings))


def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="静态泄漏检查：答案是否写进 input/ 或 task.md。")
    ap.add_argument("case_dir")
    args = ap.parse_args(argv)
    rep = check_leak(args.case_dir)
    print(rep.summary())
    return 1 if rep.leaked else 0


if __name__ == "__main__":
    raise SystemExit(_main())
