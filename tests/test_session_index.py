"""U2：双端按项目枚举 + 名片 + 中文 bigram 切词 + 关键词预筛 + 跨项目候选发现。"""

from __future__ import annotations

import json
import os

import session_index as si


# ── 切词 ────────────────────────────────────────────
def test_normalize_terms_ascii_and_cjk_bigram():
    terms = si.normalize_terms("实现 fizzbuzz(n) 工单分级")
    assert "fizzbuzz" in terms
    assert "n" in terms
    # 中文连续串切 2-gram
    assert "工单" in terms and "单分" in terms and "分级" in terms


# ── Claude 枚举 ─────────────────────────────────────
def _write_claude_session(d, name, records, *, mtime=None):
    p = d / name
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_enumerate_claude_by_project_mtime_desc(tmp_path):
    proj = tmp_path / "projects"
    enc = proj / si.se.encode_cwd("/work/p_svc")
    enc.mkdir(parents=True)
    _write_claude_session(enc, "a.jsonl", [{"type": "user", "message": {"role": "user", "content": "hi"}}], mtime=1)
    _write_claude_session(enc, "b.jsonl", [{"type": "user", "message": {"role": "user", "content": "hi"}}], mtime=10**9)
    got = si.enumerate_claude_sessions("/work/p_svc", projects_base=proj)
    assert [p.name for p in got] == ["b.jsonl", "a.jsonl"]  # 新的在前
    assert si.enumerate_claude_sessions("/work/p_svc", projects_base=proj, limit=1)[0].name == "b.jsonl"


def test_build_card_claude(tmp_path):
    proj = tmp_path / "projects"
    enc = proj / si.se.encode_cwd("/w/proj")
    enc.mkdir(parents=True)
    records = [
        {"type": "user", "cwd": "/w/proj", "message": {"role": "user", "content": "<command-name>/x</command-name>"}},
        {"type": "user", "cwd": "/w/proj", "message": {"role": "user", "content": "判断工单是否线上问题并分级"}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/w/proj/step2_analyze.py"}},
            ]},
        },
    ]
    p = _write_claude_session(enc, "s.jsonl", records)
    card = si.build_card(p, "claude")
    assert card.host == "claude"
    assert card.cwd == "/w/proj"
    assert card.first_user_goal == "判断工单是否线上问题并分级"  # 跳过 <command> 包裹
    assert "/w/proj/step2_analyze.py" in card.files_touched
    assert "Read" in card.tools_used
    assert "工单" in card.terms and "step2_analyze" in card.terms


# ── Codex 枚举（按 cwd 匹配 + 时间盒）────────────────
def _write_codex_rollout(d, name, cwd, *, goal="找出引入 bug 的 commit", mtime=None):
    records = [
        {"type": "session_meta", "payload": {"id": "x", "cwd": cwd}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": goal}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "c1"}},
    ]
    p = d / name
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_enumerate_codex_by_cwd_match(tmp_path):
    base = tmp_path / "sessions" / "2026" / "06" / "05"
    base.mkdir(parents=True)
    now = si.time.time()
    mine = _write_codex_rollout(base, "rollout-mine.jsonl", "/w/target", mtime=now)
    _write_codex_rollout(base, "rollout-other.jsonl", "/w/other", mtime=now)
    got = si.enumerate_codex_sessions("/w/target", sessions_base=tmp_path / "sessions")
    assert [p.name for p in got] == [mine.name]  # 只返回 cwd 匹配的


def test_enumerate_codex_days_timebox(tmp_path):
    base = tmp_path / "sessions" / "2026" / "01" / "01"
    base.mkdir(parents=True)
    now = si.time.time()
    _write_codex_rollout(base, "rollout-recent.jsonl", "/w/t", mtime=now)
    _write_codex_rollout(base, "rollout-stale.jsonl", "/w/t", mtime=now - 200 * 86400)  # 200 天前
    got = si.enumerate_codex_sessions("/w/t", sessions_base=tmp_path / "sessions", days=90)
    assert [p.name for p in got] == ["rollout-recent.jsonl"]  # 超 90 天的被时间盒截断


def test_build_card_codex_patch_files(tmp_path):
    base = tmp_path / "sessions"
    base.mkdir(parents=True)
    records = [
        {"type": "session_meta", "payload": {"cwd": "/w/c"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "修 foo"}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Update File: foo.py\n@@\n-a\n+b\n*** End Patch", "call_id": "c2"}},
    ]
    p = base / "rollout-x.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    card = si.build_card(p, "codex")
    assert card.cwd == "/w/c"
    assert card.first_user_goal == "修 foo"
    assert "foo.py" in card.files_touched
    assert "apply_patch" in card.tools_used


# ── 预筛 ────────────────────────────────────────────
def _card(goal, terms_extra=()):
    terms = tuple(si.normalize_terms(goal)) + tuple(terms_extra)
    return si.SessionCard(host="claude", path=f"/{goal}", mtime=1.0, first_user_goal=goal, terms=terms)


def test_prefilter_ranks_by_overlap_chinese():
    cards = [
        _card("实现一个 fizzbuzz 函数"),
        _card("判断工单是否线上问题并分级"),
        _card("写一段 sprint retro 方案"),
    ]
    ranked = si.prefilter(cards, "工单 线上问题 分级", top_k=2)
    assert ranked
    assert ranked[0].first_user_goal == "判断工单是否线上问题并分级"


def test_prefilter_empty_query_falls_back_to_recent():
    c1 = si.SessionCard(host="claude", path="/a", mtime=1.0)
    c2 = si.SessionCard(host="claude", path="/b", mtime=99.0)
    ranked = si.prefilter([c1, c2], "", top_k=1)
    assert ranked == [c2]  # 无信号 → 最近优先


# ── 跨项目候选发现（D1）─────────────────────────────
def test_scan_all_projects_for_terms(tmp_path):
    proj = tmp_path / "projects"
    a = proj / si.se.encode_cwd("/w/has_it")
    b = proj / si.se.encode_cwd("/w/cur")
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    _write_claude_session(a, "s.jsonl", [
        {"type": "user", "cwd": "/w/has_it", "message": {"role": "user", "content": "工单分级线上问题判定"}},
    ])
    _write_claude_session(b, "s.jsonl", [
        {"type": "user", "cwd": "/w/cur", "message": {"role": "user", "content": "无关任务"}},
    ])
    hits = si.scan_all_projects_for_terms(
        "工单 线上问题 分级", projects_base=proj, sessions_base=tmp_path / "none", exclude_cwd="/w/cur",
    )
    cwds = [c for c, _ in hits]
    assert "/w/has_it" in cwds  # 命中的他项目被发现
    assert "/w/cur" not in cwds  # 当前项目被排除
