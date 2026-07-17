from __future__ import annotations

import hashlib
import json
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from bench.__main__ import run_benchmark
from bench.adapters.base import Adapter, ParsedOutput
from bench.case import Case, PromptVariant, RunContract, Task
from bench.config import RunConfig
from bench.orchestrator import OrchestratorError
from bench.record import RunRecord
from bench.registry import RunnerProfile
from bench.run_manifest import (
    RunManifestError,
    load_request_manifest,
    sha256_file,
    validate_case_integrity,
    validate_provider_invariants,
)


class _CommandAdapter(Adapter):
    launcher_type = "command"

    def build_command(self, profile, prompt, workdir):
        return ["fake", prompt]

    def parse(self, stdout, stderr, exit_code):
        return ParsedOutput(is_error=exit_code not in (0, None))


def _request_manifest(prompt_hash: str = "prompt-hash", model: str = "m") -> dict:
    return {
        "schema_version": 1,
        "provider": {
            "endpoint": "https://example.invalid/anthropic",
            "requested_model": model,
            "api_path": "/v1/messages",
            "api_version": "2023-06-01",
            "temperature": 0,
            "max_tokens": 100,
            "credentials_present": True,
        },
        "wrapper_sha256": "wrapper-hash",
        "requests": [
            {
                "batch_id": "b1",
                "input_sha256": "input-hash",
                "request_prompt_sha256": prompt_hash,
                "status_code": 200,
                "auth_succeeded": True,
                "returned_model": model,
                "request_id": "req-1",
            }
        ],
    }


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _request_manifest_v2(
    *, variant: str = "a", parameters: dict | None = None, model: str = "m"
) -> dict:
    parameters = parameters or {"renderer": "a"}
    return {
        "schema_version": 2,
        "provider": {
            "endpoint": "https://example.invalid",
            "requested_model": model,
            "request_config_sha256": "a" * 64,
            "credentials_present": True,
        },
        "runtime_sha256": "b" * 64,
        "variant": {
            "label": variant,
            "parameters_sha256": _canonical_sha256(parameters),
        },
        "requests": [
            {
                "unit_id": "unit-1",
                "input_sha256": "c" * 64,
                "request_sha256": "d" * 64,
                "status_code": 200,
                "auth_succeeded": True,
                "returned_model": model,
                "provider_request_id": "req-1",
            }
        ],
    }


def _manifest_case(tmp_path: Path, name: str = "case") -> Case:
    directory = tmp_path / name
    directory.mkdir()
    return Case(
        name=name,
        directory=directory,
        task=Task(
            type="custom",
            prompt="p",
            variants=(
                PromptVariant("a", "p", {"renderer": "a"}),
                PromptVariant("b", "q", {"renderer": "b"}),
            ),
            default_variant="a",
        ),
        run_contract=RunContract("request_manifest.json", True),
    )


def _record(case: Case, tmp_path: Path, variant: str, repeat: int, manifest: dict) -> RunRecord:
    artifacts = tmp_path / f"{case.name}-{variant}-{repeat}"
    artifacts.mkdir()
    (artifacts / "request_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return RunRecord(
        case=case.name,
        runner_label="r",
        launcher_type="command",
        input_manifest_sha256="case-input-hash",
        variant_label=variant,
        repeat_index=repeat,
        artifacts_dir=str(artifacts),
    )


def test_provider_invariants_accept_expected_variant_difference(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    records = [
        _record(case, tmp_path, "a", 0, _request_manifest("a-hash")),
        _record(case, tmp_path, "a", 1, _request_manifest("a-hash")),
        _record(case, tmp_path, "b", 0, _request_manifest("b-hash")),
    ]
    summaries = validate_provider_invariants(records, {case.name: case})
    assert len(summaries) == 3


def test_provider_invariants_accept_generic_v2_manifest(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    records = [
        _record(case, tmp_path, "a", 0, _request_manifest_v2()),
        _record(
            case,
            tmp_path,
            "b",
            0,
            _request_manifest_v2(variant="b", parameters={"renderer": "b"}),
        ),
    ]

    summaries = validate_provider_invariants(records, {case.name: case})

    assert [summary["variant"] for summary in summaries] == ["a", "b"]
    assert summaries[0]["variant_context"]["parameters_sha256"] == _canonical_sha256(
        {"renderer": "a"}
    )


def test_provider_invariants_scope_request_config_to_case(tmp_path: Path) -> None:
    first_case = _manifest_case(tmp_path, "first")
    second_case = _manifest_case(tmp_path, "second")
    first = _request_manifest_v2()
    second = _request_manifest_v2()
    second["provider"]["request_config_sha256"] = "e" * 64

    summaries = validate_provider_invariants(
        [
            _record(first_case, tmp_path, "a", 0, first),
            _record(second_case, tmp_path, "a", 0, second),
        ],
        {first_case.name: first_case, second_case.name: second_case},
    )

    assert len(summaries) == 2


def test_provider_invariants_reject_request_config_drift_within_case(
    tmp_path: Path,
) -> None:
    case = _manifest_case(tmp_path)
    first = _request_manifest_v2()
    drift = _request_manifest_v2()
    drift["provider"]["request_config_sha256"] = "e" * 64

    with pytest.raises(RunManifestError, match="request 配置跨 variant/repeat 漂移"):
        validate_provider_invariants(
            [
                _record(case, tmp_path, "a", 0, first),
                _record(case, tmp_path, "a", 1, drift),
            ],
            {case.name: case},
        )


def test_provider_invariants_reject_v2_variant_contract_drift(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    manifest = _request_manifest_v2(parameters={"renderer": "wrong"})

    with pytest.raises(RunManifestError, match="variant 与框架 RUN_CONTEXT"):
        validate_provider_invariants(
            [_record(case, tmp_path, "a", 0, manifest)], {case.name: case}
        )


def test_provider_invariants_reject_repeat_prompt_drift(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    records = [
        _record(case, tmp_path, "a", 0, _request_manifest("first")),
        _record(case, tmp_path, "a", 1, _request_manifest("drift")),
    ]
    with pytest.raises(RunManifestError, match="跨 repeat 漂移"):
        validate_provider_invariants(records, {case.name: case})


def test_provider_invariants_reject_batch_input_drift(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    first = _request_manifest("a-hash")
    drift = _request_manifest("b-hash")
    drift["requests"][0]["input_sha256"] = "different-input"
    records = [
        _record(case, tmp_path, "a", 0, first),
        _record(case, tmp_path, "b", 0, drift),
    ]
    with pytest.raises(RunManifestError, match="batch 顺序或输入 hash"):
        validate_provider_invariants(records, {case.name: case})


def test_provider_manifest_rejects_model_mismatch_and_extra_provider_field(
    tmp_path: Path,
) -> None:
    case = _manifest_case(tmp_path)
    mismatch = _request_manifest()
    mismatch["requests"][0]["returned_model"] = "other"
    with pytest.raises(RunManifestError, match="返回模型与请求不一致"):
        validate_provider_invariants(
            [_record(case, tmp_path, "a", 0, mismatch)], {case.name: case}
        )

    leaky = _request_manifest()
    leaky["provider"]["authorization"] = "Bearer should-not-persist"
    with pytest.raises(RunManifestError, match="未允许字段"):
        validate_provider_invariants(
            [_record(case, tmp_path, "a", 1, leaky)], {case.name: case}
        )


def test_provider_manifest_accepts_context_window_alias_suffix(tmp_path: Path) -> None:
    """`[1m]` 等方括号后缀是请求侧路由指令（1M 上下文别名），provider 回规范名不算模型漂移。"""
    case = _manifest_case(tmp_path)
    aliased = _request_manifest(model="MiniMax-M3[1m]")
    aliased["requests"][0]["returned_model"] = "MiniMax-M3"
    validate_provider_invariants(
        [_record(case, tmp_path, "a", 0, aliased)], {case.name: case}
    )

    aliased_v2 = _request_manifest_v2(model="MiniMax-M3[1m]")
    aliased_v2["requests"][0]["returned_model"] = "MiniMax-M3"
    path = tmp_path / "request_manifest_v2.json"
    path.write_text(json.dumps(aliased_v2), encoding="utf-8")
    load_request_manifest(path)

    # 真正的模型漂移（身份不同）仍然拒绝
    aliased_v2["requests"][0]["returned_model"] = "SomeOtherModel"
    path.write_text(json.dumps(aliased_v2), encoding="utf-8")
    with pytest.raises(RunManifestError, match="返回模型与请求不一致"):
        load_request_manifest(path)


def test_provider_invariants_tolerate_partial_manifest_of_error_cell(
    tmp_path: Path,
) -> None:
    """运行失败的 cell manifest 天然不完整，不参与完整批次签名比较，不否决整个 run。"""
    case = _manifest_case(tmp_path)
    complete = _request_manifest_v2()
    complete["requests"] = complete["requests"] + [
        {**complete["requests"][0], "unit_id": "unit-2"}
    ]
    partial = _request_manifest_v2()  # 只发出了 unit-1 就失败

    healthy = _record(case, tmp_path, "a", 0, complete)
    failed = replace(_record(case, tmp_path, "a", 1, partial), is_error=True)
    summaries = validate_provider_invariants([healthy, failed], {case.name: case})
    assert [s["cell_is_error"] for s in summaries] == [False, True]

    # 健康 cell 之间的批次签名漂移仍然硬报错
    drifted = replace(_record(case, tmp_path, "a", 2, partial), is_error=False)
    with pytest.raises(RunManifestError, match="batch 顺序或输入 hash"):
        validate_provider_invariants([healthy, drifted], {case.name: case})


def test_provider_invariants_tolerate_empty_manifest_only_for_error_cell(
    tmp_path: Path,
) -> None:
    """HTTP 超时前没有成功请求时，错误 cell 可保留空 manifest，健康 cell 不可。"""
    case = _manifest_case(tmp_path)
    for repeat, (empty, error_match) in enumerate(
        [
            (_request_manifest(), "requests 不能为空"),
            (_request_manifest_v2(), "requests 必须是非空列表"),
        ]
    ):
        empty["requests"] = []
        failed = replace(_record(case, tmp_path, "a", repeat, empty), is_error=True)
        summaries = validate_provider_invariants([failed], {case.name: case})
        assert summaries[0]["cell_is_error"] is True
        assert summaries[0]["requests"] == []

        healthy = _record(case, tmp_path, "a", repeat + 10, empty)
        with pytest.raises(RunManifestError, match=error_match):
            validate_provider_invariants([healthy], {case.name: case})


def test_provider_invariants_tolerate_failed_http_entry_only_for_error_cell(
    tmp_path: Path,
) -> None:
    """非 2xx 响应没有 returned_model 是失败证据，不应否决错误 cell 的整份报告。"""
    case = _manifest_case(tmp_path)
    for repeat, failed_http in enumerate([_request_manifest(), _request_manifest_v2()]):
        request = failed_http["requests"][0]
        request["status_code"] = 529
        request.pop("returned_model")

        failed = replace(_record(case, tmp_path, "a", repeat, failed_http), is_error=True)
        summaries = validate_provider_invariants([failed], {case.name: case})
        assert summaries[0]["requests"][0]["status_code"] == 529
        assert summaries[0]["requests"][0]["returned_model"] is None

        healthy = _record(case, tmp_path, "a", repeat + 10, failed_http)
        with pytest.raises(RunManifestError, match="returned_model"):
            validate_provider_invariants([healthy], {case.name: case})


def test_error_cell_still_requires_returned_model_for_successful_request(
    tmp_path: Path,
) -> None:
    """cell 后续解析失败不能抹掉已成功 HTTP 请求的模型身份完整性。"""
    case = _manifest_case(tmp_path)
    repeat = 0
    for successful in [_request_manifest(), _request_manifest_v2()]:
        for returned_model in (None, "missing"):
            request = successful["requests"][0]
            if returned_model == "missing":
                request.pop("returned_model", None)
            else:
                request["returned_model"] = None
            record = replace(
                _record(case, tmp_path, "a", repeat, successful), is_error=True
            )
            with pytest.raises(RunManifestError, match="returned_model"):
                validate_provider_invariants([record], {case.name: case})
            repeat += 1


def test_error_cell_allows_missing_model_after_response_decode_failure(
    tmp_path: Path,
) -> None:
    manifest = _request_manifest_v2()
    request = manifest["requests"][0]
    request.pop("returned_model")
    request["failure_stage"] = "response_decode"
    path = tmp_path / "request_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = load_request_manifest(path, allow_failed_requests=True)

    assert loaded["requests"][0]["failure_stage"] == "response_decode"
    with pytest.raises(RunManifestError, match="failure_stage"):
        load_request_manifest(path)


def test_error_cell_rejects_boolean_http_status(tmp_path: Path) -> None:
    """JSON true 不能利用 Python bool 是 int 子类的特性伪装成 HTTP 状态码。"""
    case = _manifest_case(tmp_path)
    for repeat, manifest in enumerate([_request_manifest(), _request_manifest_v2()]):
        request = manifest["requests"][0]
        request["status_code"] = True
        request.pop("returned_model")
        record = replace(_record(case, tmp_path, "a", repeat, manifest), is_error=True)
        with pytest.raises(RunManifestError, match="status_code 非整数"):
            validate_provider_invariants([record], {case.name: case})


def test_provider_invariants_reject_cell_input_hash_drift(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    record = _record(case, tmp_path, "a", 0, _request_manifest())
    with pytest.raises(RunManifestError, match="cell input hash"):
        validate_provider_invariants(
            [record], {case.name: case}, {case.name: "different-case-input"}
        )


def test_case_integrity_manifest_is_verified_before_run(tmp_path: Path) -> None:
    case = _manifest_case(tmp_path)
    tracked = case.directory / "tracked.txt"
    tracked.write_text("frozen", encoding="utf-8")
    integrity = case.directory / "integrity.json"
    integrity.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "file_sha256": {"tracked.txt": sha256_file(tracked)},
            }
        ),
        encoding="utf-8",
    )
    case = Case(
        name=case.name,
        directory=case.directory,
        task=case.task,
        run_contract=RunContract(
            "request_manifest.json", True, "integrity.json"
        ),
    )
    assert validate_case_integrity(case) == {"tracked.txt": sha256_file(tracked)}
    tracked.write_text("drift", encoding="utf-8")
    with pytest.raises(RunManifestError, match="integrity hash 不一致"):
        validate_case_integrity(case)


def test_run_rechecks_case_integrity_before_completion(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "public"
    case_dir = root / "cases" / "integrity-case"
    case_dir.mkdir(parents=True)
    (case_dir / "prompt.md").write_text("prompt", encoding="utf-8")
    tracked = case_dir / "tracked.txt"
    tracked.write_text("frozen", encoding="utf-8")
    (case_dir / "integrity.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "file_sha256": {"tracked.txt": sha256_file(tracked)},
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: integrity-case
            task:
              type: prompt
              prompt_file: prompt.md
            run_contract:
              integrity_manifest_file: integrity.json
            """
        ),
        encoding="utf-8",
    )

    def mutate_case(cmd, cwd, env):
        tracked.write_text("drift", encoding="utf-8")
        return "answer", "", 0

    monkeypatch.setattr("bench.__main__.generate_run_id", lambda: "integrity-run")
    registry = {"direct": RunnerProfile("direct", "command", template="fake")}
    with pytest.raises(RunManifestError, match="integrity hash 不一致"):
        run_benchmark(
            RunConfig(runners=("direct",), cases=("integrity-case",), workers=1),
            registry,
            root,
            run_fn=mutate_case,
            adapter_factory=lambda profile: _CommandAdapter(),
        )

    manifest = json.loads(
        (root / "runs" / "integrity-run" / "run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "failed"
    assert manifest["error"]["type"] == "RunManifestError"
    assert manifest["schedule"]
    assert manifest["selection"]["variants"] == {"integrity-case": ["default"]}
    assert manifest["selection"]["repeat"] == {"integrity-case": 1}
    assert manifest["selection"]["workers"] == 1
    assert manifest["selection"]["workers_auto"] is False


def test_private_run_routes_manifest_and_scorecard(tmp_path: Path, monkeypatch) -> None:
    public_root = tmp_path / "public"
    (public_root / "cases").mkdir(parents=True)
    private_root = tmp_path / "private"
    case_dir = private_root / "cases" / "private-case"
    case_dir.mkdir(parents=True)
    (case_dir / "prompt.md").write_text("private prompt", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: private-case
            task:
              type: prompt
              prompt_file: prompt.md
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_EVAL_PRIVATE_CASES", str(private_root / "cases"))
    registry = {"direct": RunnerProfile("direct", "command", template="fake")}
    scorecard = run_benchmark(
        RunConfig(runners=("direct",), cases=("private-case",), workers=1),
        registry,
        public_root,
        run_fn=lambda cmd, cwd, env: ("answer", "", 0),
        adapter_factory=lambda profile: _CommandAdapter(),
    )
    assert scorecard.parent == private_root / "scorecards"
    run_id = scorecard.stem
    manifest = private_root / "runs" / run_id / "run_manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "complete"
    assert not (public_root / "scorecards").exists()
    assert not (public_root / "runs").exists()

    with pytest.raises(OrchestratorError, match="禁止 --write-profiles"):
        run_benchmark(
            RunConfig(runners=("direct",), cases=("private-case",), workers=1),
            registry,
            public_root,
            run_fn=lambda cmd, cwd, env: ("answer", "", 0),
            adapter_factory=lambda profile: _CommandAdapter(),
            write_profiles=True,
        )
