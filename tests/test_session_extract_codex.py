"""U2：Codex rollout 解析 + 递归定位。"""

from __future__ import annotations

import json

import session_extract as se


def _codex_fixture(tmp_path):
    records = [
        {"type": "session_meta", "payload": {}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "<permissions>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "找出引入 bug 的 commit"}]}},
        {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": '{"cmd":"git log"}', "call_id": "c1"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "commit abc123 ..."}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "input": "*** Begin Patch\n*** Update File: foo.py\n@@\n-old\n+new\n*** End Patch", "call_id": "c2"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c2", "output": "Success"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ANSWER: abc123"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "把刚才的任务抽成 case"}]}},
    ]
    p = tmp_path / "rollout-2026-06-05T10-00-00-abc.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def test_extract_codex_basic(tmp_path):
    p = _codex_fixture(tmp_path)
    ext = se.extract_session(host="codex", session_override=p)
    d = ext.digest
    assert d.host == "codex"
    texts = [t.text for t in d.turns]
    assert any("找出引入 bug" in t for t in texts)
    assert any("ANSWER: abc123" in t for t in texts)
    # developer/reasoning 不进任务对话；触发轮被剥
    assert all("permissions" not in t for t in texts)
    assert all("抽成" not in t for t in texts)


def test_extract_codex_tools_and_patch(tmp_path):
    p = _codex_fixture(tmp_path)
    ext = se.extract_session(host="codex", session_override=p)
    tool_names = {c.name for t in ext.digest.turns for c in t.tools}
    assert "exec_command" in tool_names
    assert "apply_patch" in tool_names
    # exec 结果按 call_id 配对
    exec_results = [c.result_summary for t in ext.digest.turns for c in t.tools if c.name == "exec_command"]
    assert any("commit abc123" in r for r in exec_results)
    # apply_patch → foo.py edit
    fe = {(f.path, f.op) for f in ext.digest.file_events}
    assert ("foo.py", "edit") in fe
    assert "codex-patch:c2:foo.py" in ext.content_store


def test_host_inferred_from_rollout_name(tmp_path):
    p = _codex_fixture(tmp_path)
    ext = se.extract_session(session_override=p)  # 不显式给 host，从文件名推
    assert ext.digest.host == "codex"


def test_locate_codex_recursive_newest(tmp_path):
    base = tmp_path / "sessions"
    day = base / "2026" / "06" / "05"
    day.mkdir(parents=True)
    old = day / "rollout-old.jsonl"
    new = day / "rollout-new.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    import os

    os.utime(old, (1, 1))
    os.utime(new, (10**9, 10**9))
    got = se.locate_codex_session(sessions_base=base)
    assert got == new
