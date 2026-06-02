"""runner 档案注册表（R1/R2/R3/R20）。

独立于 ~/tools/config.env 的命名档案注册表，runner 与 judge 共用。
每个档案 = 标签 → {launcher, model, config(c用), sandbox(codex用), template(command用), ...}。
校验：未知 launcher、缺失必填字段、明文密钥（须用 ${ENV} 插值）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

VALID_LAUNCHERS = ("claude", "codex", "c", "command")
VALID_METRICS = ("auto", "none")

# 明文密钥模式：OpenAI 风格 sk-、Bearer token、40+ 位 hex（典型 API key / token）。
# 凭证必须用 ${ENV_VAR} 插值，不得明文写入 runners.yaml。
_SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|[Bb]earer\s+[A-Za-z0-9._\-]{16,}"
    r"|\b[A-Fa-f0-9]{40,}\b"
)
_ENV_INTERP_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


class RegistryError(ValueError):
    """注册表加载或校验错误。"""


@dataclass(frozen=True)
class RunnerProfile:
    """一个命名启动器档案。"""

    label: str
    launcher: str
    model: str | None = None
    config: str | None = None  # c: config.env 索引
    sandbox: str | None = None  # codex: read-only | workspace-write | danger-full-access
    template: str | None = None  # command: 命令模板
    metrics: str = "auto"  # auto | none
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    def with_model(self, model: str | None) -> "RunnerProfile":
        """返回模型被覆盖的新档案（不就地改）。"""
        return replace(self, model=model)


def _iter_string_values(profile_data: dict[str, Any]) -> list[tuple[str, str]]:
    """展开档案里所有字符串值，用于密钥扫描。返回 (路径, 值)。"""
    out: list[tuple[str, str]] = []
    for key, val in profile_data.items():
        if isinstance(val, str):
            out.append((key, val))
        elif isinstance(val, (list, tuple)):
            for i, item in enumerate(val):
                if isinstance(item, str):
                    out.append((f"{key}[{i}]", item))
        elif isinstance(val, dict):
            for sub, item in val.items():
                if isinstance(item, str):
                    out.append((f"{key}.{sub}", item))
    return out


def _assert_no_plaintext_secret(label: str, profile_data: dict[str, Any]) -> None:
    for path, value in _iter_string_values(profile_data):
        # ${ENV} 插值是允许的；把插值片段挖掉后再扫，避免环境变量名误伤。
        stripped = _ENV_INTERP_RE.sub("", value)
        if _SECRET_RE.search(stripped):
            raise RegistryError(
                f"档案 '{label}' 的字段 '{path}' 疑似含明文密钥；"
                f"请改用 ${{ENV_VAR}} 环境变量插值，不要把凭证写进 runners.yaml。"
            )


def _build_profile(label: str, data: dict[str, Any]) -> RunnerProfile:
    if not isinstance(data, dict):
        raise RegistryError(f"档案 '{label}' 必须是映射，实际是 {type(data).__name__}。")

    launcher = data.get("launcher")
    if launcher not in VALID_LAUNCHERS:
        raise RegistryError(
            f"档案 '{label}' 的 launcher='{launcher}' 非法；"
            f"必须是 {', '.join(VALID_LAUNCHERS)} 之一。"
        )

    if launcher == "c" and data.get("config") is None:
        raise RegistryError(f"档案 '{label}' 是 c 类启动器，必须提供 config（config.env 索引）。")
    if launcher == "command" and not data.get("template"):
        raise RegistryError(f"档案 '{label}' 是 command 类启动器，必须提供 template。")

    metrics = data.get("metrics", "auto")
    if metrics not in VALID_METRICS:
        raise RegistryError(
            f"档案 '{label}' 的 metrics='{metrics}' 非法；必须是 {', '.join(VALID_METRICS)}。"
        )

    _assert_no_plaintext_secret(label, data)

    raw_args = data.get("args", ())
    args = tuple(str(a) for a in raw_args) if raw_args else ()
    env = {str(k): str(v) for k, v in (data.get("env") or {}).items()}
    config = data.get("config")

    return RunnerProfile(
        label=label,
        launcher=launcher,
        model=data.get("model"),
        config=str(config) if config is not None else None,
        sandbox=data.get("sandbox"),
        template=data.get("template"),
        metrics=metrics,
        args=args,
        env=env,
    )


def load_registry(path: str | Path) -> dict[str, RunnerProfile]:
    """从 runners.yaml 加载所有档案，按标签索引。"""
    p = Path(path)
    if not p.exists():
        raise RegistryError(f"runner 注册表文件不存在: {p}")

    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    runners = doc.get("runners")
    if not isinstance(runners, dict):
        raise RegistryError(f"{p} 顶层缺少 'runners:' 映射。")

    profiles: dict[str, RunnerProfile] = {}
    for label, data in runners.items():
        profiles[str(label)] = _build_profile(str(label), data)
    return profiles


def get_profile(registry: dict[str, RunnerProfile], label: str) -> RunnerProfile:
    """按标签取档案，不存在时报清晰错误。"""
    if label not in registry:
        available = ", ".join(sorted(registry)) or "(空)"
        raise RegistryError(f"未找到 runner 档案 '{label}'；可用档案: {available}。")
    return registry[label]
