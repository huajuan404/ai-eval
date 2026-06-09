"""session-to-eval U2/U3/U4：双 CLI session 定位 + 解析 → token 受限 digest。

兼容两端真实布局（本机实测）：
- Claude Code：`~/.claude/projects/<编码cwd>/<sid>.jsonl`，扁平 `user`/`assistant` 记录，
  工具调用是 `message.content[]` 内嵌 `tool_use`/`tool_result` 块；文件内容在独立 file-history 存储，
  transcript 的 `file-history-snapshot` 只存引用。
- Codex：递归 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`，`{type,payload}` 流，
  `response_item.payload.type` ∈ message/function_call/custom_tool_call/reasoning/...。

U2 提供：配置解析、host 检测、session 定位、容错解析、双适配器、触发轮 cutoff、digest 组装。
U3/U4 在本文件追加分类器与资产重建函数（计划简单优先：不另起脚本）。
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# ── 错误类型 ────────────────────────────────────────
class ConfigError(ValueError):
    """配置缺失或非法。"""


class SessionNotFound(FileNotFoundError):
    """定位不到 session log。"""


# ── 配置解析（U2/U6 共用）────────────────────────────
def load_config(skill_dir: str | Path | None = None) -> dict[str, Any]:
    """解析 skill 配置。优先级：env SESSION_TO_EVAL_CONFIG > <skill_dir>/config.toml。"""
    env = os.environ.get("SESSION_TO_EVAL_CONFIG")
    if env:
        cfg_path = Path(env)
    else:
        base = Path(skill_dir) if skill_dir else Path(__file__).resolve().parent.parent
        cfg_path = base / "config.toml"
    if not cfg_path.exists():
        raise ConfigError(
            f"未找到配置 {cfg_path}；请 `cp config.example.toml config.toml` 并填入 ai_eval_path。"
        )
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    if not data.get("ai_eval_path"):
        raise ConfigError(f"{cfg_path} 缺少 ai_eval_path。")
    return data


# ── 数据模型（不可变）────────────────────────────────
@dataclass(frozen=True)
class ToolCall:
    name: str
    args_summary: str = ""
    result_summary: str = ""


@dataclass(frozen=True)
class Turn:
    role: str  # user | assistant
    text: str
    tools: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class FileEvent:
    path: str
    op: str  # read | write | edit | snapshot | delete
    content_ref: str | None = None  # 指向 content_store（U4 取全文）


@dataclass(frozen=True)
class Digest:
    host: str
    session_path: str
    turns: tuple[Turn, ...]
    file_events: tuple[FileEvent, ...]
    usage_hints: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "session_path": self.session_path,
            "turns": [
                {
                    "role": t.role,
                    "text": t.text,
                    "tools": [
                        {"name": c.name, "args": c.args_summary, "result": c.result_summary}
                        for c in t.tools
                    ],
                }
                for t in self.turns
            ],
            "file_events": [
                {"path": f.path, "op": f.op, "content_ref": f.content_ref}
                for f in self.file_events
            ],
            "usage_hints": self.usage_hints,
        }


@dataclass(frozen=True)
class Extraction:
    digest: Digest
    content_store: Mapping[str, str]  # content_ref -> 全文（U4 资产重建用）


# ── host 检测 + session 定位 ─────────────────────────
def encode_cwd(cwd: str) -> str:
    """把 cwd 编码成 Claude Code 的 project 目录名（非字母数字 → `-`，与 Claude 实测一致）。

    实测：`/Users/d/quality-operations/defect_pipeline_service` →
    `-Users-d-quality-operations-defect-pipeline-service`（`_`/`.` 也变 `-`）。
    此编码**有损不可逆**（`a_b` 与 `a-b` 都编码成 `a-b`），正向定位用它即可；
    反向（目录名→cwd）不可靠，故 resolve_claude_project_dir 备有读真 cwd 的兜底。
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _read_session_cwd(jsonl_path: str | Path, *, scan_lines: int = 40) -> str | None:
    """读 session 文件前若干行，返回首个出现的 `cwd`（容错半写行；Claude transcript 记录带此字段）。"""
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            for _ in range(scan_lines):
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("cwd"):
                    return str(obj["cwd"])
    except OSError:
        return None
    return None


def resolve_claude_project_dir(
    cwd: str, projects_base: str | Path | None = None
) -> Path | None:
    """解析 cwd 对应的 Claude project 目录，定位失败返回 None。

    先试编码目录（修正后的 `[^a-zA-Z0-9]→-` 规则）；编码有损可能错配，故编码目录不存在时，
    扫 projects_base 下各目录、读其最新 session 首条记录的真 `cwd` 精确匹配（万无一失的兜底）。
    """
    base = Path(projects_base or Path.home() / ".claude" / "projects")
    enc = base / encode_cwd(cwd)
    if enc.exists():
        return enc
    if not base.exists():
        return None
    for d in base.iterdir():
        if not d.is_dir():
            continue
        cands = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands and _read_session_cwd(cands[0]) == cwd:
            return d
    return None


def detect_host(
    *, cwd: str | None = None, projects_base: str | Path | None = None, sessions_base: str | Path | None = None
) -> str:
    """判定当前 host：claude | codex。先看环境标记，再退化到目录启发式。"""
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE"):
        return "claude"
    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_HOME"):
        return "codex"
    cdir = resolve_claude_project_dir(cwd or os.getcwd(), projects_base)
    if cdir and any(cdir.glob("*.jsonl")):
        return "claude"
    sb = Path(sessions_base or Path.home() / ".codex" / "sessions")
    if sb.exists() and any(sb.glob("**/rollout-*.jsonl")):
        return "codex"
    raise ConfigError("无法判定 host（Claude Code / Codex）；请用 --session 显式指定 session 路径。")


def locate_claude_session(
    cwd: str, projects_base: str | Path | None = None, override: str | Path | None = None
) -> Path:
    """定位 Claude Code 当前 session：项目目录（含真 cwd 兜底）下最新 mtime 的 .jsonl。"""
    if override:
        return Path(override)
    d = resolve_claude_project_dir(cwd, projects_base)
    cands = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if d else []
    if not cands:
        raise SessionNotFound(f"Claude Code session 未找到：cwd={cwd}")
    return cands[0]


def locate_codex_session(sessions_base: str | Path | None = None, override: str | Path | None = None) -> Path:
    """定位 Codex 当前 session：递归 YYYY/MM/DD 下最新 mtime 的 rollout-*.jsonl。"""
    if override:
        return Path(override)
    base = Path(sessions_base or Path.home() / ".codex" / "sessions")
    cands = (
        sorted(base.glob("**/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if base.exists()
        else []
    )
    if not cands:
        raise SessionNotFound(f"Codex session 未找到：{base}/**/rollout-*.jsonl")
    return cands[0]


def read_jsonl_tolerant(path: str | Path) -> list[dict]:
    """容错逐行解析 JSONL：跳过空行与半写/损坏行（应对读时仍在追加的活 transcript）。"""
    out: list[dict] = []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ── 文本工具 ────────────────────────────────────────
_MARKERS = ("ANSWER:", "diff --git", "Traceback", "FAIL", "PASS", "Error:", "error:")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for b in value:
            if isinstance(b, dict) and "text" in b:
                parts.append(str(b["text"]))
            else:
                parts.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(value, ensure_ascii=False)


def truncate(text: str, limit: int = 2000) -> str:
    """超限截断，但保留含关键标记（ANSWER:/diff 头/错误栈等）的中段行。"""
    if len(text) <= limit:
        return text
    head_len = limit * 2 // 3
    tail_len = limit - head_len
    head = text[:head_len]
    tail = text[len(text) - tail_len :]
    middle = text[head_len : len(text) - tail_len]
    kept = [ln for ln in middle.splitlines() if any(m in ln for m in _MARKERS)]
    if kept:
        mid = "\n[…截断；保留标记…]\n" + "\n".join(kept[:20]) + "\n"
    else:
        mid = "\n[…截断…]\n"
    return head + mid + tail


# ── Claude 适配器 ───────────────────────────────────
def _content_blocks(record: dict) -> list:
    c = (record.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def _file_event_from_claude_tool(
    name: str, inp: dict, tid: str | None, res_text: str, store: dict[str, str]
) -> FileEvent | None:
    fp = inp.get("file_path") or inp.get("path")
    if not fp:
        return None
    if name == "Read":
        ref = f"claude-read:{tid}"
        store[ref] = res_text
        return FileEvent(fp, "read", ref)
    if name == "Write":
        ref = f"claude-write:{tid}"
        store[ref] = str(inp.get("content", ""))
        return FileEvent(fp, "write", ref)
    if name in ("Edit", "NotebookEdit"):
        ref = f"claude-edit:{tid}"
        store[ref] = json.dumps(
            {"old": inp.get("old_string", ""), "new": inp.get("new_string", "")}, ensure_ascii=False
        )
        return FileEvent(fp, "edit", ref)
    return None


def parse_claude(records: Sequence[dict]) -> tuple[list[Turn], list[FileEvent], dict[str, str]]:
    """解析 Claude Code transcript → (turns, file_events, content_store)。"""
    results: dict[str, str] = {}
    for r in records:
        for b in _content_blocks(r):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = _as_text(b.get("content"))

    turns: list[Turn] = []
    file_events: list[FileEvent] = []
    store: dict[str, str] = {}
    for r in records:
        if r.get("type") not in ("user", "assistant"):
            continue
        role = (r.get("message") or {}).get("role", r.get("type"))
        text_parts: list[str] = []
        tools: list[ToolCall] = []
        blocks = _content_blocks(r)
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                text_parts.append(str(b.get("text", "")))
            elif bt == "tool_use":
                name = str(b.get("name", ""))
                inp = b.get("input") or {}
                tid = b.get("id")
                res_text = results.get(tid, "")
                tools.append(
                    ToolCall(
                        name=name,
                        args_summary=_as_text(inp)[:600],
                        result_summary=res_text[:600],
                    )
                )
                fe = _file_event_from_claude_tool(name, inp, tid, res_text, store)
                if fe:
                    file_events.append(fe)
        c = (r.get("message") or {}).get("content")
        if isinstance(c, str) and c:
            text_parts.append(c)
        text = "\n".join(p for p in text_parts if p)
        if text or tools:
            turns.append(Turn(role=role, text=text, tools=tuple(tools)))

    # file-history-snapshot：内容在独立存储，这里只记引用，U4 按 ref 去磁盘取
    for r in records:
        if r.get("type") == "file-history-snapshot":
            snap = r.get("snapshot") or {}
            tfb = snap.get("trackedFileBackups") or {}
            for path in tfb:
                file_events.append(FileEvent(path=path, op="snapshot", content_ref=f"claude-fh:{path}"))
    return turns, file_events, store


# ── Codex 适配器 ────────────────────────────────────
def _codex_patch_files(patch: str) -> list[tuple[str, str]]:
    """从 apply_patch 文本抽 (op, path)。op ∈ add/update/delete。"""
    out: list[tuple[str, str]] = []
    for line in patch.splitlines():
        line = line.strip()
        for marker, op in (("*** Add File:", "add"), ("*** Update File:", "update"), ("*** Delete File:", "delete")):
            if line.startswith(marker):
                out.append((op, line[len(marker):].strip()))
    return out


def parse_codex(records: Sequence[dict]) -> tuple[list[Turn], list[FileEvent], dict[str, str]]:
    """解析 Codex rollout → (turns, file_events, content_store)。"""
    outputs: dict[str, str] = {}
    for r in records:
        if r.get("type") != "response_item":
            continue
        p = r.get("payload") or {}
        if p.get("type") in ("function_call_output", "custom_tool_call_output"):
            outputs[p.get("call_id")] = _as_text(p.get("output"))

    turns: list[Turn] = []
    file_events: list[FileEvent] = []
    store: dict[str, str] = {}
    for r in records:
        if r.get("type") != "response_item":
            continue
        p = r.get("payload") or {}
        pt = p.get("type")
        if pt == "message":
            role = p.get("role", "")
            if role not in ("user", "assistant"):
                continue  # developer/system 提示不算任务对话
            text = _as_text(p.get("content"))
            if text:
                turns.append(Turn(role=role, text=text))
        elif pt in ("function_call", "custom_tool_call"):
            name = str(p.get("name", ""))
            cid = p.get("call_id")
            args = p.get("arguments") if pt == "function_call" else p.get("input")
            args_text = _as_text(args)
            res = outputs.get(cid, "")
            turns.append(
                Turn(
                    role="assistant",
                    text="",
                    tools=(ToolCall(name=name, args_summary=args_text[:600], result_summary=res[:600]),),
                )
            )
            if name == "apply_patch" and isinstance(args_text, str):
                for op, path in _codex_patch_files(args_text):
                    ref = f"codex-patch:{cid}:{path}"
                    store[ref] = args_text
                    norm = {"add": "write", "update": "edit", "delete": "delete"}[op]
                    file_events.append(FileEvent(path=path, op=norm, content_ref=ref))
    return turns, file_events, store


# ── 触发轮 cutoff ───────────────────────────────────
_TRIGGERS = (
    "抽成",
    "eval case",
    "turn this into an eval",
    "benchmark case",
    "make a benchmark",
)


def is_trigger(text: str) -> bool:
    t = text.lower()
    return any(m.lower() in t for m in _TRIGGERS)


def apply_trigger_cutoff(turns: Sequence[Turn]) -> tuple[Turn, ...]:
    """丢弃最后一个触发轮及其之后的所有轮（蒸馏的是触发之前的真实任务）。"""
    idx: int | None = None
    for i, t in enumerate(turns):
        if t.role == "user" and is_trigger(t.text):
            idx = i
    return tuple(turns[:idx]) if idx is not None else tuple(turns)


# ── digest 组装 ─────────────────────────────────────
def build_digest(
    host: str,
    session_path: str,
    turns: Sequence[Turn],
    file_events: Sequence[FileEvent],
    *,
    turn_limit: int = 2000,
    tool_limit: int = 400,
) -> Digest:
    dturns = tuple(
        replace(
            t,
            text=truncate(t.text, turn_limit),
            tools=tuple(
                replace(
                    c,
                    args_summary=truncate(c.args_summary, tool_limit),
                    result_summary=truncate(c.result_summary, tool_limit),
                )
                for c in t.tools
            ),
        )
        for t in turns
    )
    hints = {
        "turn_count": len(turns),
        "tool_calls": sum(len(t.tools) for t in turns),
        "files_touched": len({f.path for f in file_events}),
    }
    return Digest(host=host, session_path=session_path, turns=dturns, file_events=tuple(file_events), usage_hints=hints)


def extract_session(
    *,
    host: str | None = None,
    cwd: str | None = None,
    session_override: str | Path | None = None,
    projects_base: str | Path | None = None,
    sessions_base: str | Path | None = None,
) -> Extraction:
    """顶层入口：定位 + 解析 + 剥触发轮 + 组装 digest。"""
    if host is None and session_override is None:
        host = detect_host(cwd=cwd, projects_base=projects_base, sessions_base=sessions_base)
    if host is None and session_override is not None:
        name = Path(session_override).name
        host = "codex" if name.startswith("rollout-") else "claude"

    if host == "claude":
        path = locate_claude_session(cwd or os.getcwd(), projects_base, session_override)
        records = read_jsonl_tolerant(path)
        turns, fes, store = parse_claude(records)
    elif host == "codex":
        path = locate_codex_session(sessions_base, session_override)
        records = read_jsonl_tolerant(path)
        turns, fes, store = parse_codex(records)
    else:
        raise ConfigError(f"未知 host：{host!r}")

    turns = apply_trigger_cutoff(turns)
    digest = build_digest(host, str(path), turns, fes)
    return Extraction(digest=digest, content_store=store)


# ── U3：确定性首过分类器 + 信号派生 ─────────────────
_CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".sh", ".sql", ".swift", ".kt", ".php",
}
_INVESTIGATION_TOOLS = {"Read", "Grep", "Glob", "Bash", "exec_command", "tool_search_call", "WebFetch", "WebSearch"}


@dataclass(frozen=True)
class TaskSignals:
    code_file_writes: int = 0      # 对代码文件的 write/edit 次数
    tool_calls: int = 0            # 工具调用总数
    investigation_tools: int = 0   # 调查型工具调用数（read/grep/git/exec/search）
    has_answer_marker: bool = False  # 输出含 ANSWER: 单值答案
    output_chars: int = 0          # 末条 assistant 文本长度
    writing_intent: bool = False   # 长文/方案/文案意图（语义判定，由 LLM 置）


@dataclass(frozen=True)
class Classification:
    cls: str           # reasoning | coding | tool-using | writing
    confidence: str    # high | low（low → 交 LLM 复核）
    reason: str


def derive_signals(
    turns: Sequence[Turn], file_events: Sequence[FileEvent], *, writing_intent: bool = False
) -> TaskSignals:
    """从一个任务切片的 turns + file_events 派生确定性信号。"""
    code_writes = sum(
        1 for f in file_events if f.op in ("write", "edit") and Path(f.path).suffix in _CODE_EXT
    )
    tool_calls = sum(len(t.tools) for t in turns)
    investigation = sum(1 for t in turns for c in t.tools if c.name in _INVESTIGATION_TOOLS)
    answer = any("ANSWER:" in t.text for t in turns if t.role == "assistant")
    asst = [t.text for t in turns if t.role == "assistant" and t.text]
    out_chars = len(asst[-1]) if asst else 0
    return TaskSignals(
        code_file_writes=code_writes,
        tool_calls=tool_calls,
        investigation_tools=investigation,
        has_answer_marker=answer,
        output_chars=out_chars,
        writing_intent=writing_intent,
    )


def classify_task(s: TaskSignals) -> Classification:
    """确定性首过分类（四类）。歧义置 low confidence → SKILL.md 让 LLM 复核改判。

    优先级：
    1. 代码改动 + 调查 + 单值答案 三者并存 → 边界冲突，候选 coding，低置信。
    2. 单值答案 + 工具调用、无代码产物 → tool-using。
    3. 有代码文件产物 → coding。
    4. 显式 writing 意图 → writing。
    5. 其余 → reasoning（长文但无 writing 意图者低置信，待 LLM 区分 reasoning/writing）。
    """
    if s.code_file_writes >= 1 and s.investigation_tools >= 3 and s.has_answer_marker:
        return Classification("coding", "low", "代码改动 + 工具调查 + 单值答案并存，边界冲突，交 LLM 复核")
    if s.has_answer_marker and s.tool_calls >= 1 and s.code_file_writes == 0:
        return Classification("tool-using", "high", "单值答案 + 工具调查、无代码产物")
    if s.code_file_writes >= 1:
        return Classification("coding", "high", "有可配测试的代码产物")
    if s.writing_intent:
        return Classification("writing", "high", "长文 / 方案 / 文案意图")
    conf = "low" if s.output_chars >= 1500 else "high"
    return Classification("reasoning", conf, "无代码无单值答案 → 推理；长文需 LLM 区分是否 writing")


# ── U4：脱敏（transcript 专属，扩展 bench/scrub 基线）─
_SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "PRIVATE_KEY"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "OPENAI_KEY"),
    (re.compile(r"\b(?:gh[pousr])_[A-Za-z0-9]{20,}\b"), "GITHUB_TOKEN"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_ACCESS_KEY"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "JWT"),
    (re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/@]+:[^\s/@]+@\S+"), "URL_CRED"),
    (re.compile(r"[Bb]earer\s+[A-Za-z0-9._\-]{16,}"), "BEARER"),
    (
        re.compile(
            r"(?i)(?:aws_secret_access_key|api[_-]?key|auth[_-]?token|secret|password|passwd|pwd|access[_-]?token|"
            r"private[_-]?key|client[_-]?secret|db[_-]?pass\w*)\s*[=:]\s*\S+"
        ),
        "KV_SECRET",
    ),
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "LONG_HEX"),
)

_HIGH_ENTROPY = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")


def scrub_secrets(text: str) -> tuple[str, list[str]]:
    """脱敏并返回命中类别。覆盖面远超 bench/scrub 的 5 类（评审 P0）。"""
    hits: list[str] = []
    out = text
    for pat, label in _SECRET_PATTERNS:
        if pat.search(out):
            hits.append(label)
            out = pat.sub(f"[REDACTED:{label}]", out)
    return out, hits


def residual_secret_risk(scrubbed: str) -> bool:
    """脱敏后仍有长的字母+数字混合 token（base64-ish / 非标键名）→ 建议人工确认。"""
    for m in _HIGH_ENTROPY.finditer(scrubbed):
        tok = m.group()
        if "REDACTED" in tok:
            continue
        if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
            return True
    return False


# ── U4：cat -n 剥离 + 资产重建 + ground-truth 分诊 ───
_CATN = re.compile(r"^\s*\d+\t")


def strip_cat_n(text: str) -> str:
    """剥除 Read tool_result 的 `cat -n` 行号前缀（多数行匹配时才剥，避免误伤含 tab 内容）。"""
    lines = text.split("\n")
    if not lines:
        return text
    matches = sum(1 for ln in lines if _CATN.match(ln))
    if matches < max(1, int(len(lines) * 0.6)):
        return text
    return "\n".join(_CATN.sub("", ln) for ln in lines)


def needs_setup_stub(turns: Sequence[Turn], file_events: Sequence[FileEvent]) -> bool:
    """工作集疑似外部大仓库（出现 git 操作 / clone）→ input 不完整，产 setup.sh 桩。"""
    for t in turns:
        for c in t.tools:
            a = (c.args_summary or "").lower()
            if "git " in a or "git\n" in a or "clone" in a or "checkout" in a:
                return True
    return False


@dataclass(frozen=True)
class ReconstructedAsset:
    path: str          # case 相对路径，如 input/solution.py
    content: str
    bucket: str        # input | verify
    synthesized: bool = False
    needs_review: bool = False


@dataclass(frozen=True)
class ReconstructionResult:
    assets: tuple[ReconstructedAsset, ...]
    setup_stub: bool            # 工作集外部 → 产 setup.sh 桩
    ground_truth_external: bool  # 真值需外部 → README TODO + expected/verify 桩
    notes: tuple[str, ...]


def _case_relpath(abspath: str, project_cwd: str = "") -> str:
    p = Path(abspath)
    if project_cwd:
        try:
            return str(p.relative_to(project_cwd))
        except ValueError:
            pass
    return p.name


def reconstruct(
    turns: Sequence[Turn],
    file_events: Sequence[FileEvent],
    content_store: Mapping[str, str],
    *,
    project_cwd: str = "",
) -> ReconstructionResult:
    """诚实分级的资产重建（session 优先 → 覆盖门 → 合成由上层补）。

    - 工作集外部（git/大仓库）→ setup_stub，不落碎片 input/。
    - 否则取每个文件首个 Read 的前态（剥 cat -n + 脱敏）→ input/。
    - ground-truth 默认需外部权威（评审实测三范本皆外部）→ TODO，不把 agent 输出当 oracle。
    """
    if needs_setup_stub(turns, file_events):
        return ReconstructionResult(
            (),
            setup_stub=True,
            ground_truth_external=True,
            notes=(
                "工作集疑似外部大仓库 / 含 git 操作：input 不完整，已标记产 setup.sh 桩；"
                "请人工补全工作集获取方式与 ground-truth。",
            ),
        )

    assets: list[ReconstructedAsset] = []
    seen: set[str] = set()
    for fe in file_events:
        if fe.op != "read" or fe.path in seen:
            continue
        raw = content_store.get(fe.content_ref or "", "")
        if not raw:
            continue
        content = strip_cat_n(raw)
        scrubbed, hits = scrub_secrets(content)
        rel = _case_relpath(fe.path, project_cwd)
        assets.append(
            ReconstructedAsset(
                path=f"input/{rel}",
                content=scrubbed,
                bucket="input",
                needs_review=bool(hits) or residual_secret_risk(scrubbed),
            )
        )
        seen.add(fe.path)

    notes: list[str] = []
    if not assets:
        notes.append("session 内无可复原的输入前态：小输入可由 LLM 合成（标 synthesized），否则留 input/ 占位说明。")
    notes.append("ground-truth 默认需外部权威：在 case.yaml 的 expected 与 verify/ 填真值（README 已标 TODO），不要把 agent 自己的输出当 oracle。")
    return ReconstructionResult(tuple(assets), setup_stub=False, ground_truth_external=True, notes=tuple(notes))


# ── U5：case 命名 / 序号（确定性，供生成器调用）──────
def next_sequence_number(cases_dir: str | Path, date_str: str) -> int:
    """扫 cases_dir 下当日已有目录，返回下一个序号（从 1 起）。"""
    base = Path(cases_dir)
    prefix = f"{date_str}-"
    maxn = 0
    if base.exists():
        for d in base.iterdir():
            if not (d.is_dir() and d.name.startswith(prefix)):
                continue
            num = d.name[len(prefix):].split("-", 1)[0]
            if num.isdigit():
                maxn = max(maxn, int(num))
    return maxn + 1


def case_dirname(date_str: str, seq: int, name: str) -> str:
    """组装 case 目录名：YYYY-MM-DD-NNN-name（NNN 3 位零填充）。"""
    return f"{date_str}-{seq:03d}-{name}"


def synthesized_asset(rel_path: str, content: str) -> ReconstructedAsset:
    """把 LLM 合成的输入落成资产（脱敏 + 标 synthesized），供 input/ 兜底。"""
    scrubbed, hits = scrub_secrets(content)
    return ReconstructedAsset(
        path=f"input/{rel_path}",
        content=scrubbed,
        bucket="input",
        synthesized=True,
        needs_review=bool(hits) or residual_secret_risk(scrubbed),
    )


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="提取当前 session digest（JSON 输出）。")
    ap.add_argument("--host", choices=["claude", "codex"], default=None)
    ap.add_argument("--session", default=None, help="显式 session 文件路径（覆盖自动定位）")
    args = ap.parse_args(argv)
    ext = extract_session(host=args.host, session_override=args.session)
    print(json.dumps(ext.digest.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
