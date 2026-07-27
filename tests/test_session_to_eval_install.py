"""U1：session-to-eval skill 安装与配置契约（精简：配置字段 + install.sh 软链行为）。"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "session-to-eval"
INSTALL_SH = SKILL_DIR / "install.sh"
SKILL_NAME = "session-to-eval"


def _run_install(skills_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLAUDE_SKILLS_DIR=str(skills_dir))
    return subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_config_example_has_ai_eval_path():
    data = tomllib.loads((SKILL_DIR / "config.example.toml").read_text(encoding="utf-8"))
    assert "ai_eval_path" in data


def test_install_creates_symlink(tmp_path):
    skills = tmp_path / "skills"
    proc = _run_install(skills)
    assert proc.returncode == 0, proc.stderr
    link = skills / SKILL_NAME
    assert link.is_symlink()
    assert link.resolve() == SKILL_DIR.resolve()


def test_install_idempotent(tmp_path):
    skills = tmp_path / "skills"
    _run_install(skills)
    proc = _run_install(skills)
    assert proc.returncode == 0, proc.stderr
    assert (skills / SKILL_NAME).is_symlink()
    assert "幂等" in proc.stdout


def test_install_refuses_non_target(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / SKILL_NAME).write_text("blocker", encoding="utf-8")
    proc = _run_install(skills)
    assert proc.returncode != 0
    assert "拒绝覆盖" in proc.stderr
