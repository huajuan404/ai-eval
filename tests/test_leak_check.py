"""静态泄漏检查：答案是否已写进 input/ 或 task.md（污染检测）。"""

from __future__ import annotations

import json
import textwrap

import leak_check as lc


def _make_case(tmp_path, *, photo: dict, task_md: str, expected_yaml: str):
    cd = tmp_path / "case"
    (cd / "input").mkdir(parents=True)
    (cd / "prompts").mkdir(parents=True)
    (cd / "input" / "photo.json").write_text(json.dumps(photo, ensure_ascii=False), encoding="utf-8")
    (cd / "prompts" / "task.md").write_text(task_md, encoding="utf-8")
    (cd / "case.yaml").write_text(expected_yaml, encoding="utf-8")
    return cd


_EXPECTED = textwrap.dedent("""\
    name: t
    class: reasoning
    expected:
      photo:
        id: IMG-001
        source_photo_id: 1001
        expected:
          is_outdoor: false
          scene_type: "室内"
          quality_grade: "清晰"
          album: "家庭"
""")

# task.md 正文不点名字段；输出 schema 里的标签放在 ```json``` 围栏内（合法枚举，不算泄漏）
_CLEAN_TASK = textwrap.dedent("""\
    # 任务：给照片分类
    根据照片描述判断场景（室内/室外）与清晰程度。
    ```json
    {"scene_type": "室内 | 室外", "quality_grade": "清晰 | 模糊"}
    ```
""")


def test_clean_case_no_leak(tmp_path):
    photo = {"id": "IMG-001", "description": "生日聚会合影，背景有餐桌", "judgment": "",
             "notes": [{"role": "photographer", "text": "在朋友家拍摄"}]}
    cd = _make_case(tmp_path, photo=photo, task_md=_CLEAN_TASK, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is False, rep.summary()


def test_nonempty_conclusion_field_in_input_is_leak(tmp_path):
    photo = {"id": "IMG-001", "description": "生日聚会合影",
             "judgment": "画面有家具且人物轮廓清楚，应归入清晰的室内照片。"}
    cd = _make_case(tmp_path, photo=photo, task_md=_CLEAN_TASK, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is True
    assert any(f.kind == "conclusion-field-input" for f in rep.findings)


def test_conclusion_field_token_in_task_is_leak(tmp_path):
    photo = {"id": "IMG-001", "description": "生日聚会合影", "judgment": ""}
    leaky_task = _CLEAN_TASK + "\n**分类说明（judgment）**：\n> 室内照片，清晰。\n"
    cd = _make_case(tmp_path, photo=photo, task_md=leaky_task, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is True
    assert any(f.kind == "conclusion-label-task" for f in rep.findings)


def test_short_labels_in_rules_not_flagged(tmp_path):
    # "室内""清晰"出现在 task 规则/schema 里属合法词汇，不应误报
    photo = {"id": "IMG-001", "description": "生日聚会合影", "judgment": ""}
    rules_task = _CLEAN_TASK + "\n规则：室内和室外照片都要分类；主体容易辨认时可标为清晰。\n"
    cd = _make_case(tmp_path, photo=photo, task_md=rules_task, expected_yaml=_EXPECTED)
    rep = lc.check_leak(cd)
    assert rep.leaked is False, rep.summary()


def test_long_answer_sentence_echoed_in_input_is_leak(tmp_path):
    sentence = "照片中的人物轮廓清晰且背景有沙发，因此归为清晰的室内合照"
    expected_yaml = textwrap.dedent(f"""\
        name: t
        class: reasoning
        expected:
          answer:
            reasoning: "{sentence}"
    """)
    photo = {"id": "IMG-001", "description": "生日聚会合影", "judgment": "",
             "note": sentence}  # 整句答案被塞进 input 普通字段
    cd = _make_case(tmp_path, photo=photo, task_md=_CLEAN_TASK, expected_yaml=expected_yaml)
    rep = lc.check_leak(cd)
    assert rep.leaked is True
    assert any(f.kind == "answer-echo-input" for f in rep.findings)
