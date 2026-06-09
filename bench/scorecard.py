"""计分卡生成 + 模型档案写入（R13/R14/R18/R21，KTD9/KTD11/KTD12）。

- 多模型并排：runner（启动器+模型）标签、四维、每维赢家、权衡摘要；
- 不自动聚合单一分（KTD12）：结论由用户判定；
- repeat N → 中位数 + 离散度，check 算 pass 率；
- 比较口径声明 harness 为已知混淆变量；
- 进入可分享 markdown 前对裁判理由脱敏。
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .orchestrator import MatrixResult
from .case import Case
from .completion import CellCompletion, cell_completion, runner_completion
from .record import RunRecord
from .scrub import scrub_truncate

_DASH = "—"


def _median(values: list[float]) -> float | None:
    nums = [v for v in values if v is not None]
    return statistics.median(nums) if nums else None


def _fmt(values: list[float | None], *, as_int: bool = False) -> str:
    """中位数 [min–max]；全 None → —。"""
    nums = [v for v in values if v is not None]
    if not nums:
        return _DASH
    med = statistics.median(nums)
    show = f"{int(round(med))}" if as_int else f"{med:g}"
    if len(nums) > 1 and min(nums) != max(nums):
        lo = int(round(min(nums))) if as_int else f"{min(nums):g}"
        hi = int(round(max(nums))) if as_int else f"{max(nums):g}"
        return f"{show} [{lo}–{hi}]"
    return show


@dataclass
class CellAgg:
    runner_label: str
    runner_model: str
    samples: int
    durations: list[float | None]
    in_tokens: list[float | None]
    out_tokens: list[float | None]
    costs: list[float | None]
    files_changed: list[float | None]
    check_pass_rate: float | None  # 0..1, None if no check
    judge_scores: list[float | None]
    judge_model: str
    reasonings: list[str]

    @property
    def duration_med(self) -> float | None:
        return _median(self.durations)

    @property
    def cost_med(self) -> float | None:
        return _median(self.costs)

    @property
    def judge_score_med(self) -> float | None:
        return _median(self.judge_scores)


def _aggregate(records: list[RunRecord]) -> CellAgg:
    checks = [r.check.passed for r in records if r.check.ran]
    pass_rate = (sum(1 for c in checks if c) / len(checks)) if checks else None
    j_model = next((r.judge.model for r in records if r.judge and r.judge.model), "")
    reasonings = [r.judge.reasoning for r in records if r.judge and r.judge.reasoning]
    return CellAgg(
        runner_label=records[0].runner_label,
        runner_model=records[0].runner_model,
        samples=len(records),
        durations=[float(r.duration_ms) for r in records],
        in_tokens=[r.usage.effective_input if r.usage else None for r in records],  # 含缓存的真实输入
        out_tokens=[r.usage.output_tokens if r.usage else None for r in records],
        costs=[r.usage.cost_usd if r.usage else None for r in records],
        files_changed=[float(r.agentic.files_changed) for r in records],
        check_pass_rate=pass_rate,
        judge_scores=[
            r.judge.score for r in records if r.judge and r.judge.ran and r.judge.score is not None
        ],
        judge_model=j_model,
        reasonings=reasonings,
    )


def _winners(cells: list[CellAgg]) -> dict[str, str]:
    """每维赢家（仅 quality/latency/cost；agentic 为无方向诊断不评赢家）。"""
    out: dict[str, str] = {}

    # quality：优先 check pass 率，其次 judge 分
    rated = [c for c in cells if c.check_pass_rate is not None or c.judge_score_med is not None]
    if rated:
        def q_key(c: CellAgg):
            return (
                c.check_pass_rate if c.check_pass_rate is not None else -1,
                c.judge_score_med if c.judge_score_med is not None else -1,
            )
        out["quality"] = max(rated, key=q_key).runner_label

    lat = [c for c in cells if c.duration_med is not None]
    if lat:
        out["latency"] = min(lat, key=lambda c: c.duration_med).runner_label

    cost = [c for c in cells if c.cost_med is not None]
    if cost:
        out["cost"] = min(cost, key=lambda c: c.cost_med).runner_label

    return out


def _bundle_label(cell: CellAgg) -> str:
    return f"{cell.runner_label}" + (f" ({cell.runner_model})" if cell.runner_model else "")


def build_scorecard(
    result: MatrixResult, *, judge_label: str = "claude", cases: dict[str, Case] | None = None
) -> str:
    """从矩阵结果生成可分享 markdown 计分卡。

    传入 `cases`（name→Case）即可算「任务完成度」列（核心结果信号）；省略则退化为旧版四维表。
    """
    cases = cases or {}
    by_case: dict[str, dict[str, list[RunRecord]]] = defaultdict(lambda: defaultdict(list))
    for rec in result.records:
        by_case[rec.case][rec.runner_label].append(rec)

    # 预算各 case×runner 完成度（需 case 对象）；comp_accum 供跨 case 汇总表
    comp_by_case: dict[str, dict[str, CellCompletion]] = {}
    comp_accum: dict[str, list[CellCompletion]] = defaultdict(list)
    for case_name, case_records in by_case.items():
        case_obj = cases.get(case_name)
        if case_obj is None:
            continue
        m: dict[str, CellCompletion] = {}
        for label, recs in case_records.items():
            cc = cell_completion(recs, case_obj)
            m[label] = cc
            comp_accum[label].append(cc)
        comp_by_case[case_name] = m

    skipped_by_case: dict[str, list] = defaultdict(list)
    for sk in result.skipped:
        skipped_by_case[sk.case].append(sk)

    all_cases = sorted(set(by_case) | set(skipped_by_case))
    runner_set = sorted({r.runner_label for r in result.records})

    lines: list[str] = []
    lines.append(f"# 计分卡 ({date.today().isoformat()})")
    lines.append("")
    lines.append(
        "> **比较口径**：每行是「启动器 + 模型」捆绑，不是裸模型；"
        "harness（system prompt / 工具 / agent loop）是已知混淆变量，"
        "结论勿上升为裸模型优劣。"
    )
    lines.append(
        f"> **裁判**：{judge_label}（advisory；有 check 时以 check pass 率为质量锚）。"
        " **结论不自动聚合**——每维赢家仅供参考，最终选择由你判定。"
    )
    lines.append(
        f"> 参与 runner：{', '.join(runner_set) or '(无)'}；用例数：{len(all_cases)}。"
    )
    lines.append("")

    # 跨用例「任务完成率」汇总（多用例时最直观的横排结果信号；单用例时见下方表内列即可）
    if len(all_cases) > 1 and comp_accum:
        summary = [runner_completion(label, cs) for label, cs in comp_accum.items()]
        summary.sort(key=lambda rc: (rc.rate if rc.rate is not None else -1.0), reverse=True)
        lines.append("## 任务完成率（跨用例汇总）")
        lines.append("")
        lines.append(
            "> 完成 = 对用例权威判据（check 通过 / judge ≥ 阈值 / 核心判据）的通过；各用例等权，"
            "无判据的用例不计入分母。"
        )
        lines.append("")
        lines.append("| Runner (启动器+模型) | 完成/适用 (率) |")
        lines.append("|---|---|")
        for rc in summary:
            lines.append(f"| {rc.runner_label} | {rc.display} |")
        lines.append("")

    for case_name in all_cases:
        lines.append(f"## 用例: {case_name}")
        lines.append("")
        case_records = by_case.get(case_name, {})
        cells = [_aggregate(recs) for recs in case_records.values()]
        cells.sort(key=lambda c: c.runner_label)
        comp_by_runner = comp_by_case.get(case_name, {})

        lines.append(
            "| Runner (启动器+模型) | 样本 | 任务完成 | check pass | judge 分 | 耗时(ms) | "
            "tokens(in✦/out) | cost($) | files_changed◇ |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for c in cells:
            comp_str = comp_by_runner[c.runner_label].display if c.runner_label in comp_by_runner else _DASH
            pass_str = (
                f"{int(round(c.check_pass_rate * c.samples))}/{c.samples}"
                if c.check_pass_rate is not None
                else _DASH
            )
            judge_str = _fmt(c.judge_scores) if c.judge_score_med is not None else _DASH
            has_tokens = any(t is not None for t in c.in_tokens + c.out_tokens)
            tok_str = (
                f"{_fmt(c.in_tokens, as_int=True)}/{_fmt(c.out_tokens, as_int=True)}"
                if has_tokens
                else _DASH
            )
            lines.append(
                f"| {_bundle_label(c)} | {c.samples} | {comp_str} | {pass_str} | {judge_str} | "
                f"{_fmt(c.durations, as_int=True)} | {tok_str} | "
                f"{_fmt(c.costs)} | {_fmt(c.files_changed, as_int=True)} |"
            )
        for sk in skipped_by_case.get(case_name, []):
            lines.append(f"| {sk.runner_label} | — | N/A | N/A | N/A | N/A | N/A | N/A | N/A |")
        lines.append("")
        lines.append(
            "✦ in 为真实总输入（含缓存读写 cache_read/creation）；cost 仅对真 Anthropic 计费的 claude 启动器显示，"
            "c 路由的第三方/本地模型显示「—」（claude 自报 cost 是按 Claude 定价的影子，非真实成本）。"
        )
        lines.append("◇ files_changed 为无方向诊断量（含创建/修改/删除），仅与 check/judge 并读，不单独评优劣。")
        lines.append("")

        winners = _winners(cells)
        if winners:
            parts = []
            if "quality" in winners:
                parts.append(f"质量={winners['quality']}")
            if "latency" in winners:
                parts.append(f"速度={winners['latency']}")
            if "cost" in winners:
                parts.append(f"成本={winners['cost']}")
            lines.append(f"**每维赢家**：{'，'.join(parts)}")
            lines.append("")
            lines.append(f"**权衡**：{_tradeoff_sentence(winners)} 结论由你判定。")
            lines.append("")

        # 裁判理由（脱敏）
        reasoning_cells = [c for c in cells if c.reasonings]
        if reasoning_cells:
            lines.append("<details><summary>裁判理由（advisory，已脱敏）</summary>")
            lines.append("")
            for c in reasoning_cells:
                for rsn in c.reasonings:
                    lines.append(f"- **{c.runner_label}**: {scrub_truncate(rsn, 600)}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    return "\n".join(lines)


def _tradeoff_sentence(winners: dict[str, str]) -> str:
    uniq = set(winners.values())
    if len(uniq) == 1:
        only = next(iter(uniq))
        return f"{only} 在所评维度上全面占优；仍需结合你对各维度的权重判断。"
    bits = []
    if "quality" in winners:
        bits.append(f"{winners['quality']} 质量最高")
    if "latency" in winners:
        bits.append(f"{winners['latency']} 最快")
    if "cost" in winners:
        bits.append(f"{winners['cost']} 最省")
    return "；".join(bits) + "——各有所长。"


# ── 模型档案写入入口（R14）────────────────────────────
_PROFILE_TEMPLATE = """# {label}

> 模型档案。记录该「启动器+模型」捆绑在本 benchmark 中的真实表现。

## 擅长场景

## 不擅长场景

## 关键差异

## 评测记录
"""


def write_model_profile(models_dir: str | Path, label: str, summary: str) -> Path:
    """把一次运行表现追加到 models/<label>.md（遵循 README 档案格式）。"""
    root = Path(models_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{label}.md"
    if not path.exists():
        path.write_text(_PROFILE_TEMPLATE.format(label=label), encoding="utf-8")
    content = path.read_text(encoding="utf-8")
    entry = f"\n- {date.today().isoformat()}: {summary.strip()}"
    if "## 评测记录" in content:
        content = content.rstrip() + entry + "\n"
    else:
        content = content.rstrip() + "\n\n## 评测记录\n" + entry + "\n"
    path.write_text(content, encoding="utf-8")
    return path
