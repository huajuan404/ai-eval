"""静态泄漏检查：答案是否已写进 input/ 或 task.md（污染检测）。"""

from __future__ import annotations

import json
import textwrap

import leak_check as lc


def _make_case(tmp_path, *, ticket: dict, task_md: str, expected_yaml: str):
    cd = tmp_path / "case"
    (cd / "input").mkdir(parents=True)
    (cd / "prompts").mkdir(parents=True)
    (cd / "input" / "ticket.json").write_text(json.dumps(ticket, ensure_ascii=False), encoding="utf-8")
    (cd / "prompts" / "task.md").write_text(task_md, encoding="utf-8")
    (cd / "case.yaml").write_text(expected_yaml, encoding="utf-8")
    return cd


_EXPECTED = textwrap.dedent("""\
    name: t
    class: reasoning
    expected:
      ticket:
        id: TKT-001
        source_ticket_id: 7164897
        expected:
          is_online_issue: false
          defect_type: "非问题"
          severity: "N/A"
          responsibility: "用户侧"
""")

# task.md 正文不点名字段；输出 schema 里的标签放在 ```json``` 围栏内（合法枚举，不算泄漏）
_CLEAN_TASK = textwrap.dedent("""\
    # 任务：判断是否线上问题
    只给原始报障与对话，自己推断责任方（系统侧/用户侧）与是否缺陷。
    ```json
    {"responsibility": "系统侧 | 用户侧", "defect_type": "非问题 | 系统缺陷"}
    ```
""")


def test_clean_case_no_leak(tmp_path):
    ticket = {"id": "TKT-001", "problem_name": "导入失败，提示找不到HC", "problem_analysis": "",
              "chat_records": [{"role": "agent", "text": "把项目编码改成不涉及"}]}
    cd = _make_case(tmp_path, ticket=ticket, task_md=_CLEAN_TASK, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is False, rep.summary()


def test_nonempty_conclusion_field_in_input_is_leak(tmp_path):
    ticket = {"id": "TKT-001", "problem_name": "导入失败",
              "problem_analysis": "属于用户侧配置问题，经办人指导用户改字段，非系统缺陷。"}
    cd = _make_case(tmp_path, ticket=ticket, task_md=_CLEAN_TASK, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is True
    assert any(f.kind == "conclusion-field-input" for f in rep.findings)


def test_snake_case_field_token_in_task_is_leak(tmp_path):
    ticket = {"id": "TKT-001", "problem_name": "导入失败", "problem_analysis": ""}
    leaky_task = _CLEAN_TASK + "\n**问题描述（problem_analysis）**：\n> 用户侧配置问题，非缺陷。\n"
    cd = _make_case(tmp_path, ticket=ticket, task_md=leaky_task, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is True
    assert any(f.kind == "conclusion-label-task" for f in rep.findings)


def test_short_labels_in_rules_not_flagged(tmp_path):
    # "用户侧""非问题"出现在 task 规则/schema 里属合法词汇，不应误报
    ticket = {"id": "TKT-001", "problem_name": "导入失败", "problem_analysis": ""}
    rules_task = _CLEAN_TASK + "\n规则：用户侧的配置错误不算缺陷；非问题也要标注。\n"
    cd = _make_case(tmp_path, ticket=ticket, task_md=rules_task, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is False, rep.summary()


def test_long_answer_sentence_echoed_in_input_is_leak(tmp_path):
    sentence = "经办人指导用户修改项目编码字段后导入成功属用户侧操作"
    expected_yaml = textwrap.dedent(f"""\
        name: t
        class: reasoning
        expected:
          answer:
            reasoning: "{sentence}"
    """)
    ticket = {"id": "TKT-001", "problem_name": "导入失败", "problem_analysis": "",
              "note": sentence}  # 整句答案被塞进 input 普通字段
    cd = _make_case(tmp_path, ticket=ticket, task_md=_CLEAN_TASK, expected_yaml=expected_yaml)
    rep = lc.check_leak(cd)
    assert rep.leaked is True
    assert any(f.kind == "answer-echo-input" for f in rep.findings)
