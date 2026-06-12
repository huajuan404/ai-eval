"""轻量进度日志（节点级，不污染 record 字段）。

走 stdlib logging 走 stderr：tests 用 caplog，CLI 用 configure() 切换 quiet。
行格式：`[tag] key=val key=val ...` —— 便于 grep / 解析。
"""

from __future__ import annotations

import logging
import re
import sys

LOGGER_NAME = "bench"

# ANSI 颜色：按日志 [tag] 匹配。
_COLORS: dict[str, str] = {
    "start": "\033[36m",       # cyan
    "done": "\033[32m",        # green
    "progress": "\033[33m",    # yellow
    "bench": "\033[1;37m",     # bold white
    "skip": "\033[2;37m",      # dim
    "score": "\033[35m",       # magenta
}
_RESET = "\033[0m"
_TAG_RE = re.compile(r"\[(\w+)\s*\]")


class _ColorFormatter(logging.Formatter):
    """自动检测 [tag] 并上色；非 TTY 输出原样文本。"""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not sys.stderr.isatty():
            return text

        def _color(m: re.Match) -> str:
            color = _COLORS.get(m.group(1))
            return f"{color}{m.group(0)}{_RESET}" if color else m.group(0)

        return _TAG_RE.sub(_color, text)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure(quiet: bool = False) -> None:
    """幂等初始化：stderr handler，quiet 时降级到 WARNING。"""
    log = get_logger()
    if not any(isinstance(h, logging.StreamHandler) for h in log.handlers):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(_ColorFormatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(h)
    log.setLevel(logging.WARNING if quiet else logging.INFO)
    log.propagate = False


def reset() -> None:
    """测试用：移除 handler 并恢复默认 propagate 行为，避免污染 caplog。"""
    log = get_logger()
    for h in list(log.handlers):
        log.removeHandler(h)
    log.setLevel(logging.NOTSET)
    log.propagate = True
