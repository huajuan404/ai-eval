"""多 case 根：公开 cases/ + 私有 AI_EVAL_PRIVATE_CASES，合并 / 标记 / 冲突优先。"""

from __future__ import annotations

import os

from bench.case import discover_cases, is_private_case, resolve_case_roots


def _mk_case(root, name):
    d = root / name
    (d / "prompts").mkdir(parents=True)
    (d / "prompts" / "task.md").write_text("do x", encoding="utf-8")
    (d / "case.yaml").write_text(
        f"name: {name}\nclass: reasoning\n"
        "task: {type: prompt, prompt_file: prompts/task.md}\n"
        "check: {type: none}\njudge: {enabled: false}\n",
        encoding="utf-8",
    )
    return d


def test_resolve_roots_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_EVAL_PRIVATE_CASES", raising=False)
    assert resolve_case_roots(tmp_path) == [tmp_path / "cases"]


def test_resolve_roots_includes_private(tmp_path, monkeypatch):
    priv = tmp_path / "priv"
    monkeypatch.setenv("AI_EVAL_PRIVATE_CASES", str(priv))
    assert resolve_case_roots(tmp_path / "repo") == [tmp_path / "repo" / "cases", priv]


def test_discover_merges_public_and_private(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "cases").mkdir(parents=True)
    _mk_case(repo / "cases", "pub-1")
    priv = tmp_path / "priv"
    priv.mkdir()
    _mk_case(priv, "priv-1")
    monkeypatch.setenv("AI_EVAL_PRIVATE_CASES", str(priv))

    cases = {c.name: c for c in discover_cases(repo)}
    assert set(cases) == {"pub-1", "priv-1"}
    assert is_private_case(cases["priv-1"], repo) is True
    assert is_private_case(cases["pub-1"], repo) is False


def test_discover_public_wins_on_name_collision(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "cases").mkdir(parents=True)
    _mk_case(repo / "cases", "dup")
    priv = tmp_path / "priv"
    priv.mkdir()
    _mk_case(priv, "dup")
    monkeypatch.setenv("AI_EVAL_PRIVATE_CASES", str(priv))

    dups = [c for c in discover_cases(repo) if c.name == "dup"]
    assert len(dups) == 1
    assert is_private_case(dups[0], repo) is False  # 公开优先


def test_multiple_private_roots_pathsep(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "cases").mkdir(parents=True)
    p1 = tmp_path / "p1"
    p1.mkdir()
    _mk_case(p1, "a")
    p2 = tmp_path / "p2"
    p2.mkdir()
    _mk_case(p2, "b")
    monkeypatch.setenv("AI_EVAL_PRIVATE_CASES", os.pathsep.join([str(p1), str(p2)]))
    assert {"a", "b"} <= {c.name for c in discover_cases(repo)}
