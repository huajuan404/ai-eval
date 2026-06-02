"""command 适配器：通用命令模板（sf cli / 自定义斜杠命令 / 任意外部 agent）。

模板占位符：{prompt} {prompt_file} {cwd} {model}
（skill/slash 类任务对 claude/c 经 prompt 字符串表达；command 模板自身可固化具体命令。）
metrics 默认 none：仅采集墙钟，token/成本降级为不可用。
"""

from __future__ import annotations

import os
import re
import shlex

from ..registry import RunnerProfile
from .base import Adapter, ParsedOutput

PROMPT_FILENAME = "PROMPT.txt"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_KNOWN_PLACEHOLDERS = {"prompt", "prompt_file", "cwd", "model"}


class CommandAdapter(Adapter):
    launcher_type = "command"
    supports_usage = False  # 通用命令无标准 usage 输出

    def build_command(
        self, profile: RunnerProfile, prompt: str, workdir: str
    ) -> list[str]:
        template = profile.template or ""
        unknown = {m.group(1) for m in _PLACEHOLDER_RE.finditer(template)} - _KNOWN_PLACEHOLDERS
        if unknown:
            raise ValueError(
                f"command 模板含未知占位符 {sorted(unknown)}；"
                f"支持的占位符: {sorted(_KNOWN_PLACEHOLDERS)}。"
            )
        values = {
            "prompt": shlex.quote(prompt),
            "prompt_file": shlex.quote(os.path.join(workdir, PROMPT_FILENAME)),
            "cwd": shlex.quote(workdir),
            "model": shlex.quote(profile.model or ""),
        }
        rendered = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)
        return ["/bin/sh", "-c", rendered]

    def parse(self, stdout: str, stderr: str, exit_code: int | None) -> ParsedOutput:
        return ParsedOutput(usage=None, num_turns=None, is_error=exit_code not in (0, None))
