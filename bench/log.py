"""轻量进度日志（节点级，不污染 record 字段）。

走 stdlib logging 走 stderr：tests 用 caplog，CLI 用 configure() 切换 quiet。
行格式：`[tag] key=val key=val ...` —— 便于 grep / 解析。
"""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "bench"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure(quiet: bool = False) -> None:
    """幂等初始化：stderr handler，quiet 时降级到 WARNING。"""
    log = get_logger()
    if not any(isinstance(h, logging.StreamHandler) for h in log.handlers):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(message)s"))
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
