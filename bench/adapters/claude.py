"""claude 适配器：claude -p --output-format json（KTD5）。"""

from __future__ import annotations

from ..record import Usage
from ..registry import RunnerProfile
from .base import Adapter, ParsedOutput, extract_result_object


class ClaudeAdapter(Adapter):
    launcher_type = "claude"
    supports_usage = True
    extra_env_keys = ("ANTHROPIC_BASE_URL",)
    credential_env_allow = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_AUTH_TOKEN",
    )

    def build_command(
        self, profile: RunnerProfile, prompt: str, workdir: str
    ) -> list[str]:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if profile.model:
            cmd += ["--model", profile.model]
        cmd += list(profile.args)
        return cmd

    def parse(self, stdout: str, stderr: str, exit_code: int | None) -> ParsedOutput:
        obj = extract_result_object(stdout)  # 挑 result 事件，跳过 init/system 前缀事件
        if obj is None:
            return ParsedOutput(usage=None, num_turns=None, is_error=exit_code not in (0, None))
        usage_obj = obj.get("usage") or {}
        usage = Usage.from_tokens(
            usage_obj.get("input_tokens"),
            usage_obj.get("output_tokens"),
            cache_creation_tokens=usage_obj.get("cache_creation_input_tokens"),
            cache_read_tokens=usage_obj.get("cache_read_input_tokens"),
            cost_usd=obj.get("total_cost_usd"),  # 真 Anthropic 计费，可信
        )
        is_error = bool(obj.get("is_error")) or (exit_code not in (0, None))
        return ParsedOutput(
            usage=usage,
            num_turns=obj.get("num_turns"),
            is_error=is_error,
        )

    def extract_final_text(self, stdout: str) -> str:
        obj = extract_result_object(stdout)
        if obj and isinstance(obj.get("result"), str):
            return obj["result"]
        return stdout
