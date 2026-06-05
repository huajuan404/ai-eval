"""U7：端到端脚本链（非真 LLM）——extract → classify → reconstruct → 组装 → validate。

证明确定性脊梁能拼出一个可被 bench 加载且 complete 的 case。
生成本体在真实运行时由 LLM 按 SKILL.md 写文件；此处测试用确定性输出扮演生成器。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import session_extract as se
import validate_case as vc

REPO = Path(__file__).resolve().parent.parent


def _claude_coding_fixture(tmp_path):
    records = [
        {"type": "user", "message": {"role": "user", "content": "在 solution.py 实现 fizzbuzz(n)"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/proj/solution.py"}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "     1\tdef fizzbuzz(n):\n     2\t    pass"}],
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
                        "input": {"file_path": "/proj/solution.py", "content": "def fizzbuzz(n):\n    return str(n)"},
                    }
                ],
            },
        },
        {"type": "user", "message": {"role": "user", "content": "把刚才的任务抽成 eval case"}},
    ]
    p = tmp_path / "sess.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def test_e2e_extract_classify_reconstruct_validate(tmp_path):
    fix = _claude_coding_fixture(tmp_path)
    ext = se.extract_session(host="claude", session_override=fix)

    # 触发轮已剥
    assert all("抽成" not in t.text for t in ext.digest.turns)

    # 分类 → coding
    sig = se.derive_signals(ext.digest.turns, ext.digest.file_events)
    assert se.classify_task(sig).cls == "coding"

    # 资产重建：前态进 input/，cat -n 已剥
    rec = se.reconstruct(ext.digest.turns, ext.digest.file_events, ext.content_store, project_cwd="/proj")
    assert rec.setup_stub is False
    inputs = {a.path: a.content for a in rec.assets}
    assert inputs["input/solution.py"] == "def fizzbuzz(n):\n    pass"

    # 扮演生成器：用确定性输出组装一个 coding case
    cases_root = tmp_path / "cases"
    name = se.case_dirname("2026-06-05", se.next_sequence_number(cases_root, "2026-06-05"), "fizzbuzz")
    cdir = cases_root / name
    (cdir / "prompts").mkdir(parents=True)
    (cdir / "input").mkdir()
    (cdir / "verify").mkdir()
    (cdir / "prompts" / "task.md").write_text("在 solution.py 实现 fizzbuzz(n)，按规格。", encoding="utf-8")
    (cdir / "prompts" / "rubric.md").write_text(
        '评分（满分 10）。返回 JSON {"score": <n>, "max": 10, "dimensions": {}, "reasoning": "x"}',
        encoding="utf-8",
    )
    (cdir / "check.sh").write_text("#!/bin/bash\npython3 test_solution.py", encoding="utf-8")
    (cdir / "verify" / "test_solution.py").write_text("from solution import fizzbuzz\nassert fizzbuzz(3) is not None\n", encoding="utf-8")
    (cdir / "input" / "solution.py").write_text(inputs["input/solution.py"], encoding="utf-8")
    (cdir / "case.yaml").write_text(
        textwrap.dedent("""\
        name: fizzbuzz
        class: coding
        task: {type: prompt, prompt_file: prompts/task.md}
        check: {type: script, script: check.sh}
        judge: {enabled: true, rubric_file: prompts/rubric.md, dimensions: [correctness]}
        expected: {synthesized: false}
        """),
        encoding="utf-8",
    )

    # 校验门：valid + complete
    res = vc.validate_case(cdir, REPO)
    assert res.valid is True, res.errors
    assert res.complete is True, res.todos


def test_e2e_multitask_digest_exposes_both_goals(tmp_path):
    records = [
        {"type": "user", "message": {"role": "user", "content": "任务A：实现 fizzbuzz"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "好的"}]}},
        {"type": "user", "message": {"role": "user", "content": "任务B：写一段 sprint retro 方案"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "方案如下..."}]}},
        {"type": "user", "message": {"role": "user", "content": "把刚才的任务抽成 case"}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    ext = se.extract_session(host="claude", session_override=p)
    texts = " ".join(t.text for t in ext.digest.turns)
    # 两个目标都暴露给 LLM 做分段；触发轮已剥
    assert "任务A" in texts and "任务B" in texts
    assert "抽成" not in texts


def test_e2e_tool_using_setup_stub_path(tmp_path):
    records = [
        {"type": "user", "message": {"role": "user", "content": "在这个仓库里找出引入 bug 的 commit"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git log --oneline -20"}}],
            },
        },
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "abc123 fix\ndef456 bug"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ANSWER: def456"}]}},
        {"type": "user", "message": {"role": "user", "content": "把刚才的任务抽成 case"}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    ext = se.extract_session(host="claude", session_override=p)

    sig = se.derive_signals(ext.digest.turns, ext.digest.file_events)
    assert se.classify_task(sig).cls == "tool-using"  # 单值答案 + 工具调查、无代码产物

    rec = se.reconstruct(ext.digest.turns, ext.digest.file_events, ext.content_store, project_cwd="/proj")
    assert rec.setup_stub is True  # git 工作集外部 → setup.sh 桩
    assert rec.ground_truth_external is True
