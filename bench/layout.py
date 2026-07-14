"""一次评测运行的产物布局 —— run 目录是全部结果的唯一归属。

拨乱反正的核心约定：**一次运行 = 一个自包含目录** `<report_root>/runs/<run_id>/`。
manifest、完整性锁、全部 cell 产物、计分卡、HTML 报告都落在这里；
case 目录（`cases/<name>/`）回归纯定义（任务 + 数据 + 真值 + 判分），不再写入运行产物。

    <report_root>/runs/<run_id>/
    ├── run_manifest.json                              # 选择、完整性锁、状态
    ├── case.lock.json
    ├── cells/<case>/<variant>/<runner>/repeat-<N>/    # 每格：run.json + raw.txt + artifacts/
    ├── scorecard.md                                   # 计分卡（scorecards/ 另存浏览副本）
    └── report.html                                    # 自包含 HTML 报告

report_root 由 case 隐私路由决定（公开 case → 仓库根；私有 case → 私有根的上级），
与 `__main__._report_context` 的既有语义一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class LayoutError(ValueError):
    """产物路径含不安全成分。"""


@dataclass(frozen=True)
class RunLayout:
    """report_root + run_id → 本次运行全部产物路径的唯一出处。"""

    report_root: Path
    run_id: str

    def __post_init__(self) -> None:
        if not _LABEL_RE.fullmatch(self.run_id):
            raise LayoutError(f"run_id 不能用于产物路径: {self.run_id!r}")

    @property
    def run_dir(self) -> Path:
        return self.report_root / "runs" / self.run_id

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "run_manifest.json"

    @property
    def case_lock_path(self) -> Path:
        return self.run_dir / "case.lock.json"

    @property
    def cells_dir(self) -> Path:
        return self.run_dir / "cells"

    @property
    def scorecard_path(self) -> Path:
        return self.run_dir / "scorecard.md"

    @property
    def report_path(self) -> Path:
        return self.run_dir / "report.html"

    def cell_dir(
        self, case_name: str, variant_label: str, runner_label: str, repeat_index: int
    ) -> Path:
        for field_name, value in (
            ("case name", case_name),
            ("variant label", variant_label),
            ("runner label", runner_label),
        ):
            if not _LABEL_RE.fullmatch(value):
                raise LayoutError(f"{field_name} 不能用于产物路径: {value!r}")
        if repeat_index < 0:
            raise LayoutError(f"repeat_index 不能为负: {repeat_index}")
        return (
            self.cells_dir
            / case_name
            / variant_label
            / runner_label
            / f"repeat-{repeat_index}"
        )
