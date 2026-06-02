"""codex 适配器：codex exec --json（KTD5/KTD8）。

- 默认 sandbox=workspace-write，否则 agent 处于只读沙箱写不了文件；
- token 须对所有 turn.completed 事件的 usage 求和（非读末尾单条）；
- codex 无 cost 字段，cost 维度降级为 None。
"""

from __future__ import annotations

import json

from ..record import Usage
from ..registry import RunnerProfile
from .base import Adapter, ParsedOutput


class CodexAdapter(Adapter):
    launcher_type = "codex"
    supports_usage = True
    extra_env_keys = ("CODEX_HOME",)
    credential_env_allow = ("OPENAI_API_KEY", "CODEX_API_KEY")

    def build_command(
        self, profile: RunnerProfile, prompt: str, workdir: str
    ) -> list[str]:
        sandbox = profile.sandbox or "workspace-write"
        cmd = [
            "codex",
            "exec",
            prompt,
            "--json",
            "-C",
            workdir,
            "--skip-git-repo-check",
            "-s",
            sandbox,
        ]
        if profile.model:
            cmd += ["-m", profile.model]
        cmd += list(profile.args)
        return cmd

    def parse(self, stdout: str, stderr: str, exit_code: int | None) -> ParsedOutput:
        turn_inputs: list[int] = []
        turn_outputs: list[int] = []
        fallback_usage: dict | None = None
        num_turns = 0
        is_error = exit_code not in (0, None)

        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type", "")
            if isinstance(etype, str) and "error" in etype.lower():
                is_error = True
            usage = evt.get("usage")
            if etype == "turn.completed" and isinstance(usage, dict):
                num_turns += 1
                if usage.get("input_tokens") is not None:
                    turn_inputs.append(int(usage["input_tokens"]))
                if usage.get("output_tokens") is not None:
                    turn_outputs.append(int(usage["output_tokens"]))
            elif isinstance(usage, dict):
                fallback_usage = usage  # 非 turn.completed 但带 usage，留作降级

        if turn_inputs or turn_outputs:
            usage = Usage.from_tokens(
                sum(turn_inputs) or None,
                sum(turn_outputs) or None,
                cost_usd=None,  # codex 无成本字段
            )
        elif fallback_usage is not None:
            usage = Usage.from_tokens(
                fallback_usage.get("input_tokens"),
                fallback_usage.get("output_tokens"),
                cost_usd=None,
            )
        else:
            usage = None

        return ParsedOutput(
            usage=usage,
            num_turns=num_turns or None,
            is_error=is_error,
        )
