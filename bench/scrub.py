"""密钥 / 凭证脱敏（R20）。

run record 原始输出、judge.reasoning、check.detail 进入可分享产物前先脱敏。
record 写入与计分卡共用本模块。
"""

from __future__ import annotations

import re

REDACTED = "***REDACTED***"

_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # OpenAI 风格
    re.compile(r"[Bb]earer\s+[A-Za-z0-9._\-]{16,}"),  # Authorization: Bearer
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),  # 长 hex（典型 token/hash）
    re.compile(r"https?://[^\s/@]+:[^\s/@]+@"),  # URL 内嵌 user:pass@
    re.compile(r"(?i)(api[_-]?key|auth[_-]?token|secret)\s*[=:]\s*\S+"),  # key=value
)


def scrub_text(text: str | None) -> str:
    """把疑似凭证替换为 ***REDACTED***。None → 空串。"""
    if not text:
        return ""
    out = text
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def scrub_truncate(text: str | None, limit: int = 4000) -> str:
    """脱敏并截断超长字段（计分卡/可分享产物用）。"""
    s = scrub_text(text)
    if len(s) > limit:
        return s[:limit] + f"\n…[truncated {len(s) - limit} chars]"
    return s
