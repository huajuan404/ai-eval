"""session-to-eval U2：双端按项目枚举历史 session + 轻量名片 + 关键词预筛 + 跨项目候选发现。

描述驱动「检索模式」的确定性脊梁。所有函数纯确定性、可单测；语义精排在 SKILL.md 由 LLM 做。

- Claude：编码项目目录（含真 cwd 兜底，复用 session_extract.resolve_claude_project_dir）下全部 .jsonl。
- Codex：sessions 平铺 YYYY/MM/DD，不按项目分目录；读每个 rollout 首行 session_meta.payload.cwd
  归属项目，90 天 / 条数时间盒（逐文件开首行有成本，必须限量）。

名片只读必要行（首个用户目标 + 文件/工具名），不全量 digest——预筛只需近似信号。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import session_extract as se

# ── 数据模型（不可变）────────────────────────────────
@dataclass(frozen=True)
class SessionCard:
    host: str  # claude | codex
    path: str
    mtime: float
    cwd: str = ""
    first_user_goal: str = ""
    files_touched: tuple[str, ...] = ()
    tools_used: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()  # 预筛用的归一化关键词（含中文 bigram）

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "path": self.path,
            "cwd": self.cwd,
            "first_user_goal": self.first_user_goal,
            "files_touched": list(self.files_touched),
            "tools_used": list(self.tools_used),
        }


# ── 归一化切词（预筛用；中文按 bigram，不引第三方分词）──
_CJK = "一-鿿"
_ASCII_TOKEN = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(rf"[{_CJK}]+")


def normalize_terms(text: str) -> list[str]:
    """切词：ASCII token 原样 + 中文连续串切成 2-gram（粗粒度，足够预筛子串命中）。"""
    text = (text or "").lower()
    out: list[str] = list(_ASCII_TOKEN.findall(text))
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


# ── Claude / Codex 名片轻量扫描 ─────────────────────
def _user_text(content) -> str:
    """从 user message content（str 或 block 列表）抽人类文本，跳过 tool_result。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") in ("text", "input_text")
        ]
        return "\n".join(p for p in parts if p)
    return ""


# 注入前言（命令包裹 / AGENTS.md / skill 注入 / 环境上下文 / caveat）——不是真实用户目标
_PREAMBLE_PREFIXES = (
    "<",
    "base directory for this skill",
    "# agents.md",
    "agents.md instructions",
    "caveat:",
    "this session is being continued",
)


def _is_preamble(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(p) for p in _PREAMBLE_PREFIXES) or "<system-reminder>" in t[:60]


def _scan_claude_card(path: Path, *, max_records: int = 400) -> tuple[str, str, list[str], list[str]]:
    goal, cwd, files, tools = "", "", [], []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if not cwd and r.get("cwd"):
                    cwd = str(r["cwd"])
                msg = r.get("message") or {}
                content = msg.get("content")
                if r.get("type") == "user" and not goal:
                    txt = _user_text(content).strip()
                    if txt and not _is_preamble(txt):  # 跳过命令/系统/注入前言
                        goal = txt
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tools.append(str(b.get("name", "")))
                            inp = b.get("input") or {}
                            fp = inp.get("file_path") or inp.get("path")
                            if fp:
                                files.append(str(fp))
    except OSError:
        pass
    return goal, cwd, files, tools


def _scan_codex_card(path: Path, *, max_records: int = 400) -> tuple[str, str, list[str], list[str]]:
    goal, cwd, files, tools = "", "", [], []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("type") == "session_meta":
                    c = (r.get("payload") or {}).get("cwd")
                    if c and not cwd:
                        cwd = str(c)
                    continue
                if r.get("type") != "response_item":
                    continue
                p = r.get("payload") or {}
                pt = p.get("type")
                if pt == "message" and p.get("role") == "user" and not goal:
                    txt = _user_text(p.get("content")).strip()
                    if txt and not _is_preamble(txt):
                        goal = txt
                elif pt in ("function_call", "custom_tool_call"):
                    name = str(p.get("name", ""))
                    tools.append(name)
                    if name == "apply_patch":
                        args = p.get("arguments") if pt == "function_call" else p.get("input")
                        for _op, fp in se._codex_patch_files(se._as_text(args)):
                            files.append(fp)
    except OSError:
        pass
    return goal, cwd, files, tools


def build_card(path: str | Path, host: str) -> SessionCard:
    """读一个 session 文件建轻量名片（不全量解析）。"""
    path = Path(path)
    if host == "claude":
        goal, cwd, files, tools = _scan_claude_card(path)
    elif host == "codex":
        goal, cwd, files, tools = _scan_codex_card(path)
    else:
        goal, cwd, files, tools = "", "", [], []
    files = tuple(dict.fromkeys(files))  # 去重保序
    tools = tuple(dict.fromkeys(tools))
    blob = " ".join([goal, " ".join(Path(f).name for f in files), " ".join(tools)])
    terms = tuple(dict.fromkeys(normalize_terms(blob)))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return SessionCard(
        host=host, path=str(path), mtime=mtime, cwd=cwd,
        first_user_goal=goal, files_touched=files, tools_used=tools, terms=terms,
    )


# ── 双端按项目枚举 ──────────────────────────────────
def enumerate_claude_sessions(
    cwd: str, projects_base: str | Path | None = None, *, limit: int = 200
) -> list[Path]:
    """按项目枚举 Claude 历史 session（mtime 倒序，至多 limit 个）。"""
    d = se.resolve_claude_project_dir(cwd, projects_base)
    if not d:
        return []
    cands = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[:limit]


def _codex_meta_cwd(path: str | Path) -> str | None:
    """读 Codex rollout 首行 session_meta.payload.cwd（只开首行，最省）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and obj.get("type") == "session_meta":
        c = (obj.get("payload") or {}).get("cwd")
        if c:
            return str(c)
    return None


def enumerate_codex_sessions(
    cwd: str, sessions_base: str | Path | None = None, *, days: int = 90, limit: int = 200
) -> list[Path]:
    """按项目枚举 Codex 历史 rollout（读首行 cwd 匹配；days / limit 时间盒）。

    mtime 倒序先看近的；遇到早于 days 截断的文件即停（更早的都过期）。
    """
    base = Path(sessions_base or Path.home() / ".codex" / "sessions")
    if not base.exists():
        return []
    cutoff = time.time() - days * 86400 if days else None
    rollouts = sorted(base.glob("**/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[Path] = []
    for p in rollouts:
        if cutoff is not None and p.stat().st_mtime < cutoff:
            break
        if _codex_meta_cwd(p) == cwd:
            out.append(p)
            if len(out) >= limit:
                break
    return out


def build_project_cards(
    cwd: str,
    *,
    projects_base: str | Path | None = None,
    sessions_base: str | Path | None = None,
    claude_limit: int = 200,
    codex_days: int = 90,
    codex_limit: int = 200,
) -> list[SessionCard]:
    """枚举当前项目双端历史 session，建名片，mtime 倒序。"""
    cards: list[SessionCard] = []
    for p in enumerate_claude_sessions(cwd, projects_base, limit=claude_limit):
        cards.append(build_card(p, "claude"))
    for p in enumerate_codex_sessions(cwd, sessions_base, days=codex_days, limit=codex_limit):
        cards.append(build_card(p, "codex"))
    cards.sort(key=lambda c: c.mtime, reverse=True)
    return cards


# ── 关键词预筛 ──────────────────────────────────────
def prefilter(cards: list[SessionCard], query: str, *, top_k: int = 8) -> list[SessionCard]:
    """按 query 与名片 terms 的重叠度打分，命中倒序取 Top-K（同分按 mtime 近优先）。

    query 为空或无有效 term → 退化为 mtime 倒序前 top_k（无信号时回退到「最近的」）。
    """
    q = {t for t in normalize_terms(query) if len(t) >= 2 or _CJK_RUN.match(t)}
    if not q:
        return sorted(cards, key=lambda c: c.mtime, reverse=True)[:top_k]
    scored: list[tuple[int, float, SessionCard]] = []
    for c in cards:
        score = len(q & set(c.terms))
        if score:
            scored.append((score, c.mtime, c))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [c for _s, _m, c in scored[:top_k]]


# ── D1：跨项目候选发现（线索检测，只读名片级）──────
def scan_all_projects_for_terms(
    query: str,
    *,
    projects_base: str | Path | None = None,
    sessions_base: str | Path | None = None,
    exclude_cwd: str | None = None,
    per_project: int = 3,
    codex_days: int = 90,
    codex_limit: int = 400,
    max_projects: int = 500,
) -> list[tuple[str, int]]:
    """扫所有项目的 session 名片，返回命中 query 的 (cwd, score) 倒序，排除 exclude_cwd。

    供 SKILL.md 在本项目信息不足时判断「是否值得问用户跨项目搜集」（D1）。
    只读名片级（首段），不解析正文；Claude 每项目取最近 per_project 个，Codex 走 days/limit 时间盒。
    """
    q = {t for t in normalize_terms(query) if len(t) >= 2 or _CJK_RUN.match(t)}
    if not q:
        return []
    by_cwd: dict[str, int] = {}

    def _account(card: SessionCard) -> None:
        if not card.cwd or card.cwd == exclude_cwd:
            return
        score = len(q & set(card.terms))
        if score:
            by_cwd[card.cwd] = max(by_cwd.get(card.cwd, 0), score)

    cbase = Path(projects_base or Path.home() / ".claude" / "projects")
    if cbase.exists():
        for d in list(cbase.iterdir())[:max_projects]:
            if not d.is_dir():
                continue
            cands = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in cands[:per_project]:
                _account(build_card(p, "claude"))

    sbase = Path(sessions_base or Path.home() / ".codex" / "sessions")
    if sbase.exists():
        cutoff = time.time() - codex_days * 86400 if codex_days else None
        rollouts = sorted(sbase.glob("**/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for n, p in enumerate(rollouts):
            if n >= codex_limit:
                break
            if cutoff is not None and p.stat().st_mtime < cutoff:
                break
            _account(build_card(p, "codex"))

    return sorted(by_cwd.items(), key=lambda kv: -kv[1])


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="按项目枚举历史 session 名片 + 关键词预筛（检索模式 smoke）。")
    ap.add_argument("--cwd", default=None, help="项目工作目录（默认当前）")
    ap.add_argument("--query", default="", help="意图描述；给出则做预筛排序")
    ap.add_argument("--cross", action="store_true", help="跨项目候选发现（D1 线索）")
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args(argv)
    import os

    cwd = args.cwd or os.getcwd()
    if args.cross:
        hits = scan_all_projects_for_terms(args.query, exclude_cwd=cwd)
        print(json.dumps([{"cwd": c, "score": s} for c, s in hits], ensure_ascii=False, indent=2))
        return 0
    cards = build_project_cards(cwd)
    ranked = prefilter(cards, args.query, top_k=args.top_k) if args.query else cards[: args.top_k]
    print(json.dumps(
        {"cwd": cwd, "total_cards": len(cards), "result": [c.to_dict() for c in ranked]},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
