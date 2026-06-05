"""U3：确定性首过分类器 + 信号派生。"""

from __future__ import annotations

import session_extract as se


def test_classify_coding():
    c = se.classify_task(se.TaskSignals(code_file_writes=2, tool_calls=3))
    assert c.cls == "coding" and c.confidence == "high"


def test_classify_tool_using():
    c = se.classify_task(se.TaskSignals(tool_calls=6, investigation_tools=6, has_answer_marker=True))
    assert c.cls == "tool-using" and c.confidence == "high"


def test_classify_reasoning():
    c = se.classify_task(se.TaskSignals(output_chars=800))
    assert c.cls == "reasoning" and c.confidence == "high"


def test_classify_writing():
    c = se.classify_task(se.TaskSignals(output_chars=3000, writing_intent=True))
    assert c.cls == "writing" and c.confidence == "high"


def test_classify_boundary_low_confidence():
    # 改了代码 + 重度调查 + 单值答案 → 冲突，低置信交复核
    c = se.classify_task(
        se.TaskSignals(code_file_writes=1, investigation_tools=4, has_answer_marker=True, tool_calls=5)
    )
    assert c.cls == "coding" and c.confidence == "low"


def test_classify_long_reasoning_low_confidence():
    # 长文但无 writing 意图 → reasoning 低置信，待 LLM 区分是否 writing
    c = se.classify_task(se.TaskSignals(output_chars=2200))
    assert c.cls == "reasoning" and c.confidence == "low"


def test_derive_signals_from_turns():
    turns = (
        se.Turn("user", "改 foo.py"),
        se.Turn("assistant", "", tools=(se.ToolCall("Read", "foo.py"), se.ToolCall("Write", "foo.py"))),
        se.Turn("assistant", "done"),
    )
    fes = (se.FileEvent("foo.py", "read"), se.FileEvent("foo.py", "write"))
    s = se.derive_signals(turns, fes)
    assert s.code_file_writes == 1  # 只有 write 算
    assert s.tool_calls == 2
    assert s.investigation_tools == 1  # Read
    assert s.output_chars == len("done")


def test_derive_signals_answer_marker():
    turns = (se.Turn("assistant", "调查结论\nANSWER: 374478a"),)
    s = se.derive_signals(turns, ())
    assert s.has_answer_marker is True
