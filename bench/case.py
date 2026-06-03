"""用例加载与隔离工作目录（R5/R6/R7/R19，KTD6）。

用例与模型解耦：case.yaml 只声明任务 + 校验 + 裁判 rubric + 输入资产 + 引擎兼容性。
每个 (case × runner) 在隔离临时目录中运行，不污染用例源目录。
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_TASK_TYPES = ("prompt", "skill", "slash", "custom")
# Case 一级类目：4 轴覆盖全栈开发者用 LLM 的主要工作类型。
VALID_CLASSES = ("reasoning", "coding", "tool-using", "writing")

# files_changed 快照忽略列表：launcher 自建/工具产物，避免污染计数与跨 launcher 可比性。
_IGNORE_DIRS = {
    ".git",
    ".omx",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    ".idea",
    ".vscode",
}
_IGNORE_FILES = {".DS_Store"}
_IGNORE_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".lock")


class CaseError(ValueError):
    """用例加载或校验错误。"""


@dataclass(frozen=True)
class Task:
    type: str
    prompt: str
    skill: str | None = None
    args: str = ""


@dataclass(frozen=True)
class CheckSpec:
    type: str = "none"  # script | none
    script: str | None = None  # 相对用例目录


@dataclass(frozen=True)
class JudgeSpec:
    enabled: bool = False
    rubric: str = ""  # rubric 文本
    dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    name: str
    directory: Path
    task: Task
    check: CheckSpec = field(default_factory=CheckSpec)
    judge: JudgeSpec = field(default_factory=JudgeSpec)
    requires_engine: str | None = None
    repeat: int | None = None  # 覆盖全局 repeat
    class_: str = "coding"  # reasoning / coding / tool-using / writing
    expected: dict = field(default_factory=dict)  # ground-truth，check.sh / judge 用

    @property
    def input_dir(self) -> Path:
        return self.directory / "input"

    @property
    def verify_dir(self) -> Path:
        """只读基准目录：check 前覆盖到产物目录，选手改不了（防篡改）。"""
        return self.directory / "verify"

    def output_dir(self, runner_label: str) -> Path:
        return self.directory / "output" / runner_label

    def supports_launcher(self, launcher_type: str) -> bool:
        """requires_engine 兼容性判断（R19）。"""
        return self.requires_engine is None or self.requires_engine == launcher_type


def _resolve_prompt(case_dir: Path, name: str, task_data: dict[str, Any]) -> Task:
    ttype = task_data.get("type")
    if ttype not in VALID_TASK_TYPES:
        raise CaseError(
            f"用例 '{name}' 的 task.type='{ttype}' 非法；必须是 {', '.join(VALID_TASK_TYPES)}。"
        )
    skill = task_data.get("skill")
    args = str(task_data.get("args", "") or "")

    if ttype in ("prompt", "custom"):
        pf = task_data.get("prompt_file")
        if not pf:
            raise CaseError(f"用例 '{name}' 的 {ttype} 任务必须提供 prompt_file。")
        path = case_dir / pf
        if not path.exists():
            raise CaseError(f"用例 '{name}' 的 prompt_file 不存在: {pf}")
        prompt = path.read_text(encoding="utf-8").strip()
    elif ttype == "skill":
        if not skill:
            raise CaseError(f"用例 '{name}' 的 skill 任务必须提供 skill 名。")
        prompt = f"/{skill} {args}".strip()
    else:  # slash
        cmd = task_data.get("command") or skill
        if not cmd:
            raise CaseError(f"用例 '{name}' 的 slash 任务必须提供 command。")
        prompt = f"{cmd} {args}".strip()

    return Task(type=ttype, prompt=prompt, skill=skill, args=args)


def load_case(case_dir: str | Path) -> Case:
    """加载并校验一个用例目录。"""
    directory = Path(case_dir)
    manifest = directory / "case.yaml"
    if not manifest.exists():
        raise CaseError(f"用例缺少 case.yaml: {directory}")

    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    name = str(data.get("name") or directory.name)

    task_data = data.get("task")
    if not isinstance(task_data, dict):
        raise CaseError(f"用例 '{name}' 缺少 task: 映射。")
    task = _resolve_prompt(directory, name, task_data)

    check_data = data.get("check") or {}
    check_type = check_data.get("type", "none")
    if check_type not in ("script", "none"):
        raise CaseError(f"用例 '{name}' 的 check.type='{check_type}' 非法（script|none）。")
    if check_type == "script" and not check_data.get("script"):
        raise CaseError(f"用例 '{name}' 的 script 校验必须提供 script 路径。")
    check = CheckSpec(type=check_type, script=check_data.get("script"))

    judge_data = data.get("judge") or {}
    rubric = ""
    if judge_data.get("enabled"):
        rf = judge_data.get("rubric_file")
        if rf:
            rpath = directory / rf
            if not rpath.exists():
                raise CaseError(f"用例 '{name}' 的 rubric_file 不存在: {rf}")
            rubric = rpath.read_text(encoding="utf-8").strip()
    judge = JudgeSpec(
        enabled=bool(judge_data.get("enabled")),
        rubric=rubric,
        dimensions=tuple(judge_data.get("dimensions", []) or []),
    )

    repeat = data.get("repeat")
    class_ = str(data.get("class") or "coding")
    if class_ not in VALID_CLASSES:
        raise CaseError(
            f"用例 '{name}' 的 class='{class_}' 非法；必须是 {', '.join(VALID_CLASSES)}。"
        )
    expected = data.get("expected") or {}
    if not isinstance(expected, dict):
        raise CaseError(f"用例 '{name}' 的 expected 必须是映射。")
    return Case(
        name=name,
        directory=directory,
        task=task,
        check=check,
        judge=judge,
        requires_engine=data.get("requires_engine"),
        repeat=int(repeat) if repeat is not None else None,
        class_=class_,
        expected=dict(expected),
    )


def list_cases(cases_root: str | Path) -> list[Case]:
    """加载 cases/ 下所有含 case.yaml 的用例。"""
    root = Path(cases_root)
    out: list[Case] = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "case.yaml").exists():
            out.append(load_case(child))
    return out


# ── 隔离工作目录 ─────────────────────────────────────
def _should_ignore(rel_parts: tuple[str, ...], name: str) -> bool:
    if any(part in _IGNORE_DIRS for part in rel_parts):
        return True
    if name in _IGNORE_FILES:
        return True
    return name.endswith(_IGNORE_SUFFIXES)


def snapshot_dir(path: str | Path) -> dict[str, tuple[int, int]]:
    """对目录做文件签名快照（相对路径 → (size, mtime_ns)），应用忽略列表。"""
    base = Path(path)
    snap: dict[str, tuple[int, int]] = {}
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(base)
        if _should_ignore(rel.parts[:-1], f.name):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        snap[str(rel)] = (st.st_size, st.st_mtime_ns)
    return snap


def count_changed(before: dict[str, tuple], after: dict[str, tuple]) -> int:
    """统计 created + modified + deleted 文件数（无方向诊断量，KTD5）。"""
    created = set(after) - set(before)
    deleted = set(before) - set(after)
    modified = {k for k in set(before) & set(after) if before[k] != after[k]}
    return len(created) + len(deleted) + len(modified)


def copy_artifacts(workdir: str | Path, dest: str | Path) -> None:
    """把 workdir 内容（排除忽略项）复制到 output/<runner>/。

    复制前清空 dest，避免上次运行的残留产物污染本次 check/judge（重跑误判）。
    """
    src = Path(workdir)
    dst = Path(dest)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        if _should_ignore(rel.parts[:-1], f.name):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


def restore_verify_assets(case: Case, target_dir: str | Path) -> None:
    """把 case verify/ 的只读基准文件覆盖到 target_dir（check 前调用，防选手篡改测试）。

    verify/ 不进入选手 workdir（只 input/ 进），所以选手看不到也改不了基准测试；
    check 时还原基准，确保确定性质量锚可信。
    """
    if not case.verify_dir.exists():
        return
    dst = Path(target_dir)
    for f in case.verify_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(case.verify_dir)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


@contextlib.contextmanager
def isolated_workdir(case: Case) -> Iterator[Path]:
    """创建隔离临时工作目录并拷入用例 input/，退出时清理（finally）。"""
    tmp = Path(tempfile.mkdtemp(prefix=f"bench-{case.name}-"))
    try:
        if case.input_dir.exists():
            for f in case.input_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(case.input_dir)
                    target = tmp / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
