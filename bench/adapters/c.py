"""c 适配器：复用 PATH 上的 c <config> 作为无头启动器（KTD3）。

c 末尾是 `exec claude ... "$@"`，因此 `c <config> -p ... --output-format json`
会把无头参数透传给 claude。stdout 前有 c 的 banner，解析时先跳过前缀。
usage 与 claude 同为单 JSON，复用 claude 解析逻辑。
"""

from __future__ import annotations

from ..record import Usage
from ..registry import RunnerProfile
from .base import Adapter, ParsedOutput, extract_result_object


class CAdapter(Adapter):
    launcher_type = "c"
    supports_usage = True
    # c 自身从脚本同目录的 config.env 注入 token，无需父环境凭证。

    def build_command(
        self, profile: RunnerProfile, prompt: str, workdir: str
    ) -> list[str]:
        # config 必填（registry 已校验）；模型由 config.env 的 CONFIG_<n> 决定，不传 --model。
        cmd = ["c", str(profile.config), "-p", prompt, "--output-format", "json"]
        cmd += list(profile.args)
        return cmd

    def parse(self, stdout: str, stderr: str, exit_code: int | None) -> ParsedOutput:
        # 与 claude 相同：跳过 banner 前缀与 init/system 事件，挑 result 事件。
        obj = extract_result_object(stdout)
        if obj is None:
            return ParsedOutput(usage=None, num_turns=None, is_error=exit_code not in (0, None))
        usage_obj = obj.get("usage") or {}
        usage = Usage.from_tokens(
            usage_obj.get("input_tokens"),
            usage_obj.get("output_tokens"),
            cost_usd=obj.get("total_cost_usd"),
        )
        is_error = bool(obj.get("is_error")) or (exit_code not in (0, None))
        return ParsedOutput(usage=usage, num_turns=obj.get("num_turns"), is_error=is_error)

    def extract_final_text(self, stdout: str) -> str:
        obj = extract_result_object(stdout)
        if obj and isinstance(obj.get("result"), str):
            return obj["result"]
        return stdout
