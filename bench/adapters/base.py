"""适配器接口与共享工具（KTD2/KTD5/KTD6）。

每个适配器知道三件事：
  (a) 如何构造命令 build_command
  (b) 如何注入最小环境 build_env（剔除无关凭证，R20）
  (c) 如何从输出解析 usage / num_turns / is_error  parse
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..record import Usage
from ..registry import RunnerProfile

# 子进程最小环境白名单：只保留 launcher 运行所必需的通用变量，
# 不转发任何凭证类变量（R20：选手模型进程不该看到 API key）。
ESSENTIAL_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TZ",
)

# 凭证类变量名模式：构造最小环境时即便在白名单外也显式排除（双保险）。
_CREDENTIAL_KEY_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE
)
_ENV_INTERP_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class ParsedOutput:
    """适配器从启动器输出解析出的结构化结果。"""

    usage: Usage | None = None
    num_turns: int | None = None
    is_error: bool = False


def build_minimal_env(
    profile: RunnerProfile,
    base_env: dict[str, str] | None = None,
    extra_keys: tuple[str, ...] = (),
) -> dict[str, str]:
    """构造最小子进程环境：白名单通用变量 + 档案声明的 ${ENV} 插值，剔除凭证。"""
    src = base_env if base_env is not None else dict(os.environ)
    env: dict[str, str] = {}
    for key in ESSENTIAL_ENV_KEYS + extra_keys:
        if key in src and not _CREDENTIAL_KEY_RE.search(key):
            env[key] = src[key]
    # 档案 env 声明：解析 ${ENV} 插值（值从父环境取，但不污染白名单逻辑）。
    for k, v in profile.env.items():
        env[k] = _ENV_INTERP_RE.sub(lambda m: src.get(m.group(1), ""), v)
    return env


def extract_json_object(text: str) -> dict | None:
    """从可能带前缀（如 c 的 banner）的 stdout 中提取首个完整 JSON 对象。"""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def normalize_model_label(label: str) -> str:
    """规范化 model 标签用于 same_source 比较：去掉 [1m] 等后缀残留与空白。"""
    return re.sub(r"\[[^\]]*\]", "", label or "").strip()


class Adapter(ABC):
    """启动器适配器接口。"""

    launcher_type: str = ""
    supports_usage: bool = True

    @abstractmethod
    def build_command(
        self, profile: RunnerProfile, prompt: str, workdir: str
    ) -> list[str]:
        """构造无头执行命令 argv。"""

    def build_env(
        self, profile: RunnerProfile, base_env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """构造最小子进程环境（默认实现，子类可加 extra_keys）。"""
        return build_minimal_env(profile, base_env)

    @abstractmethod
    def parse(self, stdout: str, stderr: str, exit_code: int | None) -> ParsedOutput:
        """解析启动器输出。无法解析 usage 时返回 usage=None（优雅降级）。"""
