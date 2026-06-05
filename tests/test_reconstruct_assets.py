"""U4：资产重建 + ground-truth 分诊 + cat -n 剥离。"""

from __future__ import annotations

import session_extract as se


def test_strip_cat_n():
    src = "     1\t# title\n     2\t\n     3\tcode here"
    assert se.strip_cat_n(src) == "# title\n\ncode here"


def test_strip_cat_n_leaves_non_numbered():
    src = "def f():\n\treturn 1"  # 含 tab 但非行号前缀
    assert se.strip_cat_n(src) == src


def test_reconstruct_read_pre_state_to_input():
    turns = (se.Turn("user", "实现 fizzbuzz"),)
    fes = (se.FileEvent("/proj/solution.py", "read", "r1"),)
    store = {"r1": "     1\tdef fizzbuzz(n):\n     2\t    pass"}
    res = se.reconstruct(turns, fes, store, project_cwd="/proj")
    assert res.setup_stub is False
    assert len(res.assets) == 1
    a = res.assets[0]
    assert a.path == "input/solution.py"
    assert a.content == "def fizzbuzz(n):\n    pass"  # cat -n 已剥
    assert a.bucket == "input"
    assert res.ground_truth_external is True


def test_reconstruct_secret_flags_needs_review():
    turns = (se.Turn("user", "改配置"),)
    fes = (se.FileEvent("/proj/.env", "read", "r1"),)
    store = {"r1": "DB_PASSWORD=s3cr3tValue"}
    res = se.reconstruct(turns, fes, store, project_cwd="/proj")
    assert res.assets[0].needs_review is True
    assert "s3cr3tValue" not in res.assets[0].content


def test_reconstruct_setup_stub_for_git_worktree():
    turns = (
        se.Turn("user", "找出引入 bug 的 commit"),
        se.Turn("assistant", "", tools=(se.ToolCall("Bash", "git log --oneline"),)),
    )
    fes = ()
    res = se.reconstruct(turns, fes, {}, project_cwd="/proj")
    assert res.setup_stub is True
    assert res.assets == ()
    assert res.ground_truth_external is True


def test_reconstruct_no_assets_notes_ground_truth():
    res = se.reconstruct((se.Turn("user", "算笔账"),), (), {}, project_cwd="/proj")
    assert res.assets == ()
    assert res.setup_stub is False
    assert any("ground-truth" in n for n in res.notes)


def test_synthesized_asset_marks_synthesized():
    a = se.synthesized_asset("data.csv", "a,b\n1,2")
    assert a.synthesized is True
    assert a.path == "input/data.csv"
    assert a.bucket == "input"


def test_needs_setup_stub_false_without_git():
    turns = (se.Turn("assistant", "", tools=(se.ToolCall("Read", "foo.py"),)),)
    assert se.needs_setup_stub(turns, ()) is False
