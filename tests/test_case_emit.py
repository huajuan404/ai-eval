"""U5：case 命名 / 序号（确定性部分）。"""

from __future__ import annotations

import session_extract as se


def test_next_sequence_empty(tmp_path):
    assert se.next_sequence_number(tmp_path, "2026-06-05") == 1


def test_next_sequence_missing_dir(tmp_path):
    assert se.next_sequence_number(tmp_path / "nope", "2026-06-05") == 1


def test_next_sequence_increments(tmp_path):
    (tmp_path / "2026-06-05-001-foo").mkdir()
    (tmp_path / "2026-06-05-002-bar").mkdir()
    (tmp_path / "2026-06-04-009-other-day").mkdir()  # 不同日期不计
    assert se.next_sequence_number(tmp_path, "2026-06-05") == 3


def test_next_sequence_ignores_non_matching(tmp_path):
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "2026-06-05-001-foo").mkdir()
    assert se.next_sequence_number(tmp_path, "2026-06-05") == 2


def test_case_dirname_zero_pads():
    assert se.case_dirname("2026-06-05", 2, "sprint-retro") == "2026-06-05-002-sprint-retro"
