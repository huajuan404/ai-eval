"""U2：Claude Code transcript 解析 + 定位 + 容错 + 触发轮 cutoff。"""

from __future__ import annotations

import json

import pytest
import session_extract as se


def _claude_fixture(tmp_path):
    records = [
        {"type": "user", "message": {"role": "user", "content": "在 solution.py 实现 fizzbuzz(n)"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "我先读一下"},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x/solution.py"}},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "def fizzbuzz(n):\n    pass", "is_error": False}
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Write",
                        "input": {"file_path": "/x/solution.py", "content": "def fizzbuzz(n):\n    return 'x'"},
                    }
                ],
            },
        },
        {"type": "file-history-snapshot", "snapshot": {"trackedFileBackups": {"/x/solution.py": {"v": 1}}}},
        {"type": "user", "message": {"role": "user", "content": "把刚才的任务抽成一个 eval case"}},
    ]
    p = tmp_path / "sess.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def test_extract_claude_basic(tmp_path):
    p = _claude_fixture(tmp_path)
    ext = se.extract_session(host="claude", session_override=p)
    d = ext.digest
    assert d.host == "claude"
    # 触发轮被剥除：没有任何 turn 文本含 "抽成"
    assert all("抽成" not in t.text for t in d.turns)
    # 任务首轮还在
    assert any("fizzbuzz" in t.text for t in d.turns)


def test_extract_claude_file_events_and_store(tmp_path):
    p = _claude_fixture(tmp_path)
    ext = se.extract_session(host="claude", session_override=p)
    ops = {(f.path, f.op) for f in ext.digest.file_events}
    assert ("/x/solution.py", "read") in ops
    assert ("/x/solution.py", "write") in ops
    assert ("/x/solution.py", "snapshot") in ops
    assert ext.content_store["claude-read:t1"] == "def fizzbuzz(n):\n    pass"
    assert "return 'x'" in ext.content_store["claude-write:t2"]
    assert ext.digest.usage_hints["files_touched"] == 1


def test_tool_call_captured(tmp_path):
    p = _claude_fixture(tmp_path)
    ext = se.extract_session(host="claude", session_override=p)
    names = {c.name for t in ext.digest.turns for c in t.tools}
    assert "Read" in names and "Write" in names


def test_read_jsonl_tolerant_skips_partial(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n{"type":"assist', encoding="utf-8")
    recs = se.read_jsonl_tolerant(p)
    assert len(recs) == 1  # 半写的最后一行被跳过


def test_encode_cwd():
    assert se.encode_cwd("/Users/d/ai-eval") == "-Users-d-ai-eval"


def test_encode_cwd_non_alnum_to_dash():
    # 下划线、点都编码成 `-`（与 Claude Code 实测一致），修复历史定位 bug
    assert (
        se.encode_cwd("/Users/d/quality-operations/defect_pipeline_service")
        == "-Users-d-quality-operations-defect-pipeline-service"
    )
    assert se.encode_cwd("/a/b.c_d") == "-a-b-c-d"


def test_resolve_project_dir_encoded(tmp_path):
    proj = tmp_path / "projects"
    enc = proj / se.encode_cwd("/work/my_proj")
    enc.mkdir(parents=True)
    (enc / "s.jsonl").write_text("{}", encoding="utf-8")
    assert se.resolve_claude_project_dir("/work/my_proj", projects_base=proj) == enc


def test_resolve_project_dir_fallback_by_real_cwd(tmp_path):
    # 目录名故意与编码不符，靠读首条记录的真 cwd 兜底命中
    proj = tmp_path / "projects"
    weird = proj / "totally-unrelated-name"
    weird.mkdir(parents=True)
    rec = {"type": "user", "cwd": "/work/odd_proj", "message": {"role": "user", "content": "hi"}}
    (weird / "s.jsonl").write_text(json.dumps(rec), encoding="utf-8")
    assert se.resolve_claude_project_dir("/work/odd_proj", projects_base=proj) == weird


def test_resolve_project_dir_none_when_absent(tmp_path):
    assert se.resolve_claude_project_dir("/nope", projects_base=tmp_path / "projects") is None


def test_locate_claude_with_underscore_cwd(tmp_path):
    proj = tmp_path / "projects"
    enc = proj / se.encode_cwd("/work/svc_a")  # -work-svc-a
    enc.mkdir(parents=True)
    (enc / "only.jsonl").write_text("{}", encoding="utf-8")
    assert se.locate_claude_session("/work/svc_a", projects_base=proj) == enc / "only.jsonl"


def test_locate_claude_newest_mtime(tmp_path):
    proj = tmp_path / "projects"
    enc = proj / se.encode_cwd("/work/proj")
    enc.mkdir(parents=True)
    old = enc / "old.jsonl"
    new = enc / "new.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    import os

    os.utime(old, (1, 1))
    os.utime(new, (10**9, 10**9))
    got = se.locate_claude_session("/work/proj", projects_base=proj)
    assert got == new


def test_locate_claude_missing_raises(tmp_path):
    with pytest.raises(se.SessionNotFound):
        se.locate_claude_session("/nope", projects_base=tmp_path / "projects")


def test_truncate_keeps_answer_marker():
    body = "x" * 5000 + "\nANSWER: deadbeef\n" + "y" * 5000
    out = se.truncate(body, limit=300)
    assert "ANSWER: deadbeef" in out
    assert len(out) < len(body)


def test_trigger_cutoff_no_trigger_keeps_all():
    turns = (se.Turn("user", "do a thing"), se.Turn("assistant", "done"))
    assert se.apply_trigger_cutoff(turns) == turns
