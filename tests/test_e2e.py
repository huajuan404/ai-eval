"""U8: 种子用例端到端集成测试。

真实跑通：用例加载 → 隔离 workdir → agent 产物 → 真实 check.sh(bash/python3)
→ 判分 → 计分卡。仅 stub 外部 LLM 子进程（runner 输出与 judge 打分）。
"""

from __future__ import annotations

import json
from pathlib import Path

from bench.adapters.base import Adapter, ParsedOutput
from bench.case import list_cases
from bench.config import RunConfig
from bench.orchestrator import MatrixResult, run_matrix
from bench.record import Usage
from bench.registry import RunnerProfile, load_registry
from bench.scorecard import build_scorecard
from bench.scoring import score_record

ROOT = Path(__file__).resolve().parent.parent
SEED = "2026-06-02-001-fizzbuzz"

CORRECT_SOLUTION = (
    "def fizzbuzz(n):\n"
    "    if n % 15 == 0:\n"
    "        return 'FizzBuzz'\n"
    "    if n % 3 == 0:\n"
    "        return 'Fizz'\n"
    "    if n % 5 == 0:\n"
    "        return 'Buzz'\n"
    "    return str(n)\n"
)
WRONG_SOLUTION = "def fizzbuzz(n):\n    return str(n)\n"


class _FakeAdapter(Adapter):
    launcher_type = "codex"

    def build_command(self, profile, prompt, workdir):
        return ["fake", prompt]

    def build_env(self, profile, base_env=None):
        return {"PATH": "/usr/bin"}

    def parse(self, stdout, stderr, exit_code):
        return ParsedOutput(usage=Usage.from_tokens(1200, 300, 0.004), num_turns=2, is_error=False)


def _agent_run_fn(solution: str):
    def fn(cmd, cwd, env):
        Path(cwd, "solution.py").write_text(solution, encoding="utf-8")
        return json.dumps({"is_error": False, "result": "done"}), "", 0

    return fn


def _seed_case():
    cases = list_cases(ROOT / "cases")
    case = next(c for c in cases if c.name == SEED)
    return cases, case


def test_seed_case_full_chain_passing_runner() -> None:
    cases, seed = _seed_case()
    reg = {"codex": RunnerProfile("codex", "codex")}
    cfg = RunConfig(runners=("codex",), cases=(SEED,), repeat=1)

    result = run_matrix(
        cfg, reg, cases,
        run_fn=_agent_run_fn(CORRECT_SOLUTION),
        adapter_factory=lambda p: _FakeAdapter(),
    )
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.usage.total_tokens == 1500
    assert rec.agentic.files_changed >= 1  # solution.py 被改写

    # 真实 check（bash → python3 test_solution.py）+ stub judge
    judge_json = '{"score": 10, "max": 10, "dimensions": {"correctness": 7, "code_quality": 3}, "reasoning": "correct"}'
    scored = score_record(
        seed, rec, RunnerProfile("claude", "claude"),
        judge_run_fn=lambda c, w, e: (judge_json, "", 0),
        adapter_factory=lambda p: _FakeAdapter(),
    )
    assert scored.check.ran and scored.check.passed is True  # 真实测试通过
    assert "ALL PASS" in scored.check.detail
    assert scored.judge.score == 10
    assert scored.judge.same_source is False

    md = build_scorecard(MatrixResult(records=[scored]), judge_label="claude")
    assert SEED in md
    assert "1/1" in md  # check pass 率


def test_seed_case_full_chain_failing_runner() -> None:
    cases, seed = _seed_case()
    reg = {"codex": RunnerProfile("codex", "codex")}
    cfg = RunConfig(runners=("codex",), cases=(SEED,), repeat=1)

    result = run_matrix(
        cfg, reg, cases,
        run_fn=_agent_run_fn(WRONG_SOLUTION),
        adapter_factory=lambda p: _FakeAdapter(),
    )
    rec = result.records[0]
    scored = score_record(
        seed, rec, None,  # judge 关掉，只看 check
        judge_run_fn=lambda c, w, e: ("", "", 0),
        adapter_factory=lambda p: _FakeAdapter(),
    )
    # 错误实现 → check 失败（真实 python3 断言失败）
    assert scored.check.ran and scored.check.passed is False
