"""Codex review 修复的回归测试（env 隔离 / 残留清理 / 超时 / fail-fast / 防篡改）。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from bench.__main__ import ROOT, run_benchmark
from bench.adapters.base import Adapter, ParsedOutput, minimal_os_env
from bench.case import copy_artifacts, load_case
from bench.config import RunConfig
from bench.orchestrator import OrchestratorError, _select_cases, run_subprocess
from bench.registry import RegistryError, RunnerProfile
from bench.scoring import _decode, _default_script_runner, run_check

SEED = "2026-06-02-001-fizzbuzz"


class _FakeAdapter(Adapter):
    launcher_type = "codex"

    def build_command(self, profile, prompt, workdir):
        return ["fake", prompt]

    def build_env(self, profile, base_env=None):
        return {"PATH": os.environ.get("PATH", "")}

    def parse(self, stdout, stderr, exit_code):
        return ParsedOutput(is_error=False)


# ── P1: check 环境隔离 ───────────────────────────────
def test_default_script_runner_excludes_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "leaky-value")
    script = tmp_path / "s.sh"
    script.write_text("#!/bin/bash\necho \"K=$MY_API_KEY\"\n", encoding="utf-8")
    env = minimal_os_env()  # 剔除凭证类变量
    out, _, code = _default_script_runner(["bash", str(script)], str(tmp_path), env)
    assert code == 0
    assert "K=" in out and "leaky-value" not in out  # 凭证未泄露给 check 子进程


# ── P1: 产物目录复制前清空 ───────────────────────────
def test_copy_artifacts_clears_stale(tmp_path) -> None:
    wd1 = tmp_path / "wd1"
    wd1.mkdir()
    (wd1 / "a.txt").write_text("1", encoding="utf-8")
    dest = tmp_path / "dest"
    copy_artifacts(wd1, dest)
    assert (dest / "a.txt").exists()

    wd2 = tmp_path / "wd2"
    wd2.mkdir()
    (wd2 / "b.txt").write_text("2", encoding="utf-8")
    copy_artifacts(wd2, dest)
    assert (dest / "b.txt").exists()
    assert not (dest / "a.txt").exists()  # 上次残留已清


# ── P2: 超时显式失败 + 解码 ──────────────────────────
def test_run_subprocess_timeout_marks_failed() -> None:
    out, err, code = run_subprocess(
        ["sleep", "5"], cwd=".", env={"PATH": os.environ.get("PATH", "")}, timeout=1
    )
    assert code == 124  # 非 None/0 → is_error
    assert "[timeout]" in err


def test_decode_handles_bytes() -> None:
    assert _decode(b"hello") == "hello"
    assert _decode("plain") == "plain"
    assert _decode(None) == ""


# ── P2: 未知用例 fail-fast ───────────────────────────
def test_select_cases_unknown_raises() -> None:
    with pytest.raises(OrchestratorError, match="不存在"):
        _select_cases(RunConfig(cases=("ghost-case",)), [])


# ── P2: 防篡改（verify 还原）─────────────────────────
def _make_tamper_case(tmp_path: Path) -> Path:
    d = tmp_path / "tcase"
    (d / "verify").mkdir(parents=True)
    (d / "case.yaml").write_text(
        "name: tcase\ntask:\n  type: prompt\n  prompt_file: p.md\n"
        "check:\n  type: script\n  script: check.sh\n",
        encoding="utf-8",
    )
    (d / "p.md").write_text("impl f() returning 42", encoding="utf-8")
    (d / "check.sh").write_text("#!/bin/bash\npython3 test_solution.py\n", encoding="utf-8")
    # 只读基准：严格测试（不在选手 workdir 中）
    (d / "verify" / "test_solution.py").write_text(
        "from solution import f\nassert f() == 42\nprint('OK')\n", encoding="utf-8"
    )
    return d


def test_run_check_restores_verify_over_tampered_test(tmp_path) -> None:
    case = load_case(_make_tamper_case(tmp_path))
    # 模拟选手产物：错误 solution + 篡改成 trivial 的同名测试
    art = tmp_path / "art"
    art.mkdir()
    (art / "solution.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    (art / "test_solution.py").write_text("print('OK')\n", encoding="utf-8")  # 篡改

    res = run_check(case, art)
    # verify 还原严格测试 → 错误 solution 被抓出，check 失败（篡改无效）
    assert res.ran and res.passed is False


def test_run_check_passes_correct_solution_with_verify(tmp_path) -> None:
    case = load_case(_make_tamper_case(tmp_path))
    art = tmp_path / "art"
    art.mkdir()
    (art / "solution.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    res = run_check(case, art)
    assert res.ran and res.passed is True
    assert "OK" in res.detail


# ── P2: judge 标签校验 ───────────────────────────────
def test_run_benchmark_bogus_judge_fails(tmp_path, monkeypatch) -> None:
    # 真实种子用例（judge enabled）+ 不存在的 judge 标签 → 应报错而非静默跳过
    root = tmp_path / "public"
    shutil.copytree(ROOT / "cases" / SEED, root / "cases" / SEED)
    reg = {"codex": RunnerProfile("codex", "codex")}
    cfg = RunConfig(runners=("codex",), cases=(SEED,), judge="ghost-judge")

    def fake_run(cmd, cwd, env):
        Path(cwd, "solution.py").write_text("def fizzbuzz(n): return str(n)\n", encoding="utf-8")
        return "{}", "", 0

    with pytest.raises(RegistryError, match="ghost-judge"):
        run_benchmark(
            cfg, reg, root,
            run_fn=fake_run,
            adapter_factory=lambda p: _FakeAdapter(),
            judge_run_fn=lambda c, w, e: ("{}", "", 0),
        )
    manifests = list((root / "runs").glob("*/run_manifest.json"))
    assert len(manifests) == 1
    failed = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["error"]["type"] == "RegistryError"
