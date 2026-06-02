"""启动器适配器：统一 run record 输出契约 + 各自不同的命令构造/模型注入（KTD2）。"""

from __future__ import annotations

from ..registry import RunnerProfile
from .base import Adapter, ParsedOutput, build_minimal_env, extract_json_object, normalize_model_label


def get_adapter(profile: RunnerProfile) -> Adapter:
    """按档案的 launcher 类型返回对应适配器实例（惰性导入，避免循环/启动开销）。"""
    launcher = profile.launcher
    if launcher == "claude":
        from .claude import ClaudeAdapter

        return ClaudeAdapter()
    if launcher == "codex":
        from .codex import CodexAdapter

        return CodexAdapter()
    if launcher == "c":
        from .c import CAdapter

        return CAdapter()
    if launcher == "command":
        from .command import CommandAdapter

        return CommandAdapter()
    raise ValueError(f"无适配器支持 launcher='{launcher}'")  # registry 已校验，兜底


__all__ = [
    "Adapter",
    "ParsedOutput",
    "build_minimal_env",
    "extract_json_object",
    "normalize_model_label",
    "get_adapter",
]
