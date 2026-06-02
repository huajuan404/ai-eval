"""U4: 矩阵 × repeat 执行 + 指标采集。"""

from __future__ import annotations

import textwrap
from pathlib import Path

from bench.adapters.base import Adapter, ParsedOutput
from bench.case import load_case
from bench.config import RunConfig
from bench.orchestrator import run_matrix
from bench.record import Usage
from bench.registry import RunnerProfile


class FakeAdapter(Adapter):
    def __init__(self, launcher_type: str, parsed: ParsedOutput):
        self._lt = launcher_type
        self._parsed = parsed

    @property
    def launcher_type(self) -> str:  # type: ignore[override]
        return self._lt

    def build_command(self, profile, prompt, workdir):
        return ["fake", prompt]

    def build_env(self, profile, base_env=None):
        return {"PATH": "/usr/bin"}

    def parse(self, stdout, stderr, exit_code):
        return self._parsed


def _make_case(tmp_path: Path, name: str, requires_engine: str | None = None) -> Path:
    d = tmp_path / name
    (d / "input").mkdir(parents=True)
    (d / "input" / "data.txt").write_text("seed", encoding="utf-8")
    extra = f"requires_engine: {requires_engine}\n" if requires_engine else ""
    (d / "case.yaml").write_text(
        textwrap.dedent(
            f"""
            name: {name}
            task:
              type: prompt
              prompt_file: prompts/task.md
            {extra}"""
        ),
        encoding="utf-8",
    )
    (d / "prompts").mkdir()
    (d / "prompts" / "task.md").write_text("do it", encoding="utf-8")
    return d


def _fixed_clock():
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += 0.5
        return state["t"]

    return clock


def _make_run_fn(*, create_files: int = 1, create_noise: bool = False, raises: bool = False):
    def fn(cmd, cwd, env):
        if raises:
            raise FileNotFoundError("no such binary")
        for i in range(create_files):
            Path(cwd, f"out{i}.txt").write_text("x", encoding="utf-8")
        if create_noise:
            (Path(cwd) / ".omx").mkdir(exist_ok=True)
            (Path(cwd) / ".omx" / "sess.json").write_text("{}", encoding="utf-8")
        return "{}", "", 0

    return fn


def test_matrix_2runners_1case_repeat2(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1"))
    reg = {
        "a": RunnerProfile("a", "claude"),
        "b": RunnerProfile("b", "codex"),
    }
    parsed = ParsedOutput(usage=Usage.from_tokens(10, 5), num_turns=2, is_error=False)
    cfg = RunConfig(runners=("a", "b"), repeat=2)
    res = run_matrix(
        cfg, reg, [case],
        run_fn=_make_run_fn(),
        adapter_factory=lambda p: FakeAdapter("claude" if p.label == "a" else "codex", parsed),
        clock=_fixed_clock(),
        now=lambda: "2026-06-02T00:00:00Z",
    )
    assert len(res.records) == 4
    assert {r.repeat_index for r in res.records} == {0, 1}
    assert {r.runner_label for r in res.records} == {"a", "b"}


def test_empty_cases_runs_all(tmp_path: Path) -> None:
    cases = [load_case(_make_case(tmp_path, "c1")), load_case(_make_case(tmp_path, "c2"))]
    reg = {"a": RunnerProfile("a", "claude")}
    cfg = RunConfig(runners=("a",), cases=())
    res = run_matrix(
        cfg, reg, cases,
        run_fn=_make_run_fn(),
        adapter_factory=lambda p: FakeAdapter("claude", ParsedOutput()),
        clock=_fixed_clock(),
    )
    assert {r.case for r in res.records} == {"c1", "c2"}


def test_usage_filled_and_degraded(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1"))
    reg = {"a": RunnerProfile("a", "claude"), "n": RunnerProfile("n", "command", template="x", metrics="none")}
    cfg = RunConfig(runners=("a", "n"))

    def factory(p):
        if p.label == "a":
            return FakeAdapter("claude", ParsedOutput(usage=Usage.from_tokens(3, 4), is_error=False))
        fa = FakeAdapter("command", ParsedOutput(usage=Usage.from_tokens(9, 9)))
        return fa

    res = run_matrix(
        cfg, reg, [case], run_fn=_make_run_fn(),
        adapter_factory=factory, clock=_fixed_clock(),
    )
    by_label = {r.runner_label: r for r in res.records}
    assert by_label["a"].usage.total_tokens == 7
    # metrics=none → usage 降级为 None，即便 parse 给了值
    assert by_label["n"].usage is None


def test_cell_failure_does_not_abort(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1"))
    reg = {"good": RunnerProfile("good", "claude"), "bad": RunnerProfile("bad", "codex")}
    cfg = RunConfig(runners=("good", "bad"))

    def run_fn(cmd, cwd, env):
        # 'bad' 的命令以 fake 开头但我们用 label 区分；这里统一行为，靠 raises 标志
        raise FileNotFoundError("boom")

    # good 用正常 run_fn，bad 用抛异常的：用 adapter 无法区分 run_fn，改为都抛，验证两格都 is_error 但都产出 record
    res = run_matrix(
        cfg, reg, [case], run_fn=run_fn,
        adapter_factory=lambda p: FakeAdapter(p.launcher, ParsedOutput()),
        clock=_fixed_clock(),
    )
    assert len(res.records) == 2
    assert all(r.is_error for r in res.records)
    assert all(r.exit_code is None for r in res.records)


def test_wall_clock_recorded(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1"))
    reg = {"a": RunnerProfile("a", "claude")}
    res = run_matrix(
        RunConfig(runners=("a",)), reg, [case],
        run_fn=_make_run_fn(),
        adapter_factory=lambda p: FakeAdapter("claude", ParsedOutput()),
        clock=_fixed_clock(),
    )
    assert res.records[0].duration_ms == 500  # 每次 clock +0.5s


def test_files_changed_ignores_noise(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1"))
    reg = {"a": RunnerProfile("a", "claude")}
    res = run_matrix(
        RunConfig(runners=("a",)), reg, [case],
        run_fn=_make_run_fn(create_files=2, create_noise=True),
        adapter_factory=lambda p: FakeAdapter("claude", ParsedOutput()),
        clock=_fixed_clock(),
    )
    # 2 个真实文件，.omx 噪声不计
    assert res.records[0].agentic.files_changed == 2


def test_requires_engine_incompatible_skipped(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1", requires_engine="claude"))
    reg = {"x": RunnerProfile("x", "codex")}
    res = run_matrix(
        RunConfig(runners=("x",)), reg, [case],
        run_fn=_make_run_fn(),
        adapter_factory=lambda p: FakeAdapter("codex", ParsedOutput()),
        clock=_fixed_clock(),
    )
    assert res.records == []
    assert len(res.skipped) == 1
    assert res.skipped[0].runner_label == "x"
    assert res.skipped[0].case == "c1"


def test_run_record_persisted_to_disk(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, "c1"))
    reg = {"a": RunnerProfile("a", "claude")}
    run_matrix(
        RunConfig(runners=("a",), repeat=2), reg, [case],
        run_fn=_make_run_fn(),
        adapter_factory=lambda p: FakeAdapter("claude", ParsedOutput()),
        clock=_fixed_clock(),
    )
    out = case.output_dir("a")
    assert (out / "run.0.json").exists()
    assert (out / "run.1.json").exists()
    assert (out / "run.0.raw.txt").exists()
