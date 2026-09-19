"""发布门槛：只入库白名单字段与交付物，任何本机路径、凭证、篡改都整次拒绝且不落盘。

夹具从公开账本反向重建一个 `runs/<run_id>/` 目录（账本本身就是已审核的公开数据），
再往里塞私有噪音，验证发布器只带走该带的。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bench.case import load_case
from bench.models import ModelBook
from bench.publish import (
    ROOT,
    SITE,
    PublishError,
    cell_relative,
    hashes,
    public_text,
    publish_run,
    sha256,
    validate_svg,
)

RUN_ID = "20260915T124011-4c12491f"


def _ledger() -> dict:
    return json.loads((SITE / "data" / "runs" / f"{RUN_ID}.json").read_text(encoding="utf-8"))


def source_run(tmp_path: Path, document: dict) -> Path:
    """按账本记录重建运行目录，并混入不该公开的文件与字段。"""
    run = tmp_path / document["run_id"]
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({"status": "complete", "judge": document["judge"]}))
    for row in document["records"]:
        case = load_case(ROOT / "cases" / row["case"])
        cell = run / "cells" / row["case"] / row["variant_label"] / row["runner_label"] / f"repeat-{row['repeat_index']}"
        artifacts = cell / "artifacts"
        artifacts.mkdir(parents=True)
        for artifact in row["artifacts"]:
            target = artifacts / Path(artifact["path"]).relative_to(cell_relative_from_row(row))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SITE / artifact["path"]).read_bytes())
        (artifacts / "PROMPT.txt").write_text(case.task.prompt)
        (artifacts / "RUN_CONTEXT.json").write_text(row["context"])
        (artifacts / "input-manifest.json").write_text("[]")
        (artifacts / "OUTPUT.txt").write_text("private final response mentioning /Users/private-person")
        (artifacts / "credentials.txt").write_text("api_key=sk-private0123456789abcdef")
        record = {k: v for k, v in row.items() if k not in {"verdict", "criteria_sha256", "cost_usd", "price_version",
                                                             "cost_source", "output", "artifacts", "context"}}
        record["usage"] = {**(row["usage"] or {}), "cost_usd": None}
        record["check"] = {"ran": row["check"]["ran"], "passed": row["check"]["passed"],
                           "detail": "/Users/private-person/check.log", "report": {}}
        record["human_note"] = "/Users/private-person/notes.txt"
        record["artifacts_dir"] = str(artifacts)
        (cell / "run.json").write_text(json.dumps(record, ensure_ascii=False))
        (cell / "raw.txt").write_text("private launcher log")
    return run


def cell_relative_from_row(row: dict) -> Path:
    return (Path("data") / "cells" / row["case"] / row["runner_label"] / row["run_id"]
            / row["variant_label"] / f"repeat-{row['repeat_index']}")


@pytest.fixture
def document():
    return _ledger()


def test_publish_reproduces_ledger_without_private_fields(tmp_path, document):
    run = source_run(tmp_path, document)
    site = tmp_path / "site"
    written = publish_run(run, site, book=ModelBook.load())
    assert [r["case"] for r in written["records"]] == [r["case"] for r in document["records"]]
    for got, want in zip(written["records"], document["records"]):
        for key in ("case", "runner_label", "run_id", "duration_ms", "judge", "verdict", "output", "artifacts", "context"):
            assert got[key] == want[key], key
        assert "human_note" not in got and "artifacts_dir" not in got and "detail" not in got["check"]
    text = (site / "data" / "runs" / f"{RUN_ID}.json").read_text()
    assert "private-person" not in text and "sk-private" not in text
    published = sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())
    assert all(p.endswith(".svg") or p.endswith(f"{RUN_ID}.json") for p in published)
    assert not any(name in p for p in published for name in ("raw.txt", "OUTPUT.txt", "credentials.txt"))


def test_private_path_in_judge_reasoning_rejects_whole_run_before_writing(tmp_path, document):
    run = source_run(tmp_path, document)
    path = max(run.glob("cells/*/*/*/*/run.json"))
    record = json.loads(path.read_text())
    record["judge"]["reasoning"] += " see /Users/private-person/file.txt"
    path.write_text(json.dumps(record))
    site = tmp_path / "site"
    with pytest.raises(PublishError, match="本机路径"):
        publish_run(run, site)
    assert not site.exists()


def test_prompt_drift_and_context_drift_are_rejected(tmp_path, document):
    run = source_run(tmp_path, document)
    path = min(run.glob("cells/*/*/*/*/run.json"))
    record = json.loads(path.read_text())
    record["prompt_template_sha256"] = "0" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(PublishError, match="提示词"):
        publish_run(run, tmp_path / "a")
    record["prompt_template_sha256"] = document["records"][0]["prompt_template_sha256"]
    path.write_text(json.dumps(record))
    (path.parent / "artifacts" / "RUN_CONTEXT.json").write_text("{}")
    with pytest.raises(PublishError, match="RUN_CONTEXT"):
        publish_run(run, tmp_path / "b")


def test_artifact_symlink_and_oversize_are_rejected(tmp_path, document):
    run = source_run(tmp_path, document)
    row = document["records"][0]
    svg = run / "cells" / row["case"] / row["variant_label"] / row["runner_label"] / f"repeat-{row['repeat_index']}" / "artifacts" / Path(row["output"]).name
    outside = tmp_path / "outside.svg"
    outside.write_bytes(svg.read_bytes())
    svg.unlink()
    svg.symlink_to(outside)
    with pytest.raises(PublishError, match="符号链接"):
        publish_run(run, tmp_path / "a")
    svg.unlink()
    svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">' + b"<!-- " + b"x" * (2 * 1024 * 1024) + b" --></svg>")
    with pytest.raises(PublishError, match="上限"):
        publish_run(run, tmp_path / "b")


def test_filters_and_republish_merge(tmp_path, document):
    run = source_run(tmp_path, document)
    site = tmp_path / "site"
    first = publish_run(run, site, runners={"kimi-k3"})
    assert {r["runner_label"] for r in first["records"]} == {"kimi-k3"}
    second = publish_run(run, site, runners={"glm-5.3"})
    assert {r["runner_label"] for r in second["records"]} == {"kimi-k3", "glm-5.3"}
    assert len(second["records"]) == 4
    with pytest.raises(PublishError, match="没有符合"):
        publish_run(run, site, cases={"no-such-case"})


def test_only_public_cases_and_complete_runs(tmp_path, document):
    run = source_run(tmp_path, document)
    (run / "run_manifest.json").write_text(json.dumps({"status": "running"}))
    with pytest.raises(PublishError, match="complete"):
        publish_run(run, tmp_path / "a")
    (run / "run_manifest.json").write_text(json.dumps({"status": "complete", "judge": "claude"}))
    path = min(run.glob("cells/*/*/*/*/run.json"))
    record = json.loads(path.read_text())
    record["case"] = "../../private-root/secret-case"
    path.write_text(json.dumps(record))
    with pytest.raises(PublishError):
        publish_run(run, tmp_path / "b")


@pytest.mark.parametrize("markup", [
    '<script>alert(1)</script>',
    '<circle onload="alert(1)"/>',
    '<use href="https://example.com/hidden.svg#shape"/>',
    '<rect fill="url(https://example.com/pixel)"/>',
    '<foreignObject/>',
])
def test_public_svg_rejects_active_or_external_content(markup):
    with pytest.raises(PublishError):
        validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{markup}</svg>'.encode())


def test_public_text_allows_listed_hashes_but_not_home_paths():
    digest = sha256(b"artifact")
    public_text(json.dumps({"artifacts": [{"sha256": digest}]}), hashes([{"artifacts": [{"sha256": digest}]}]))
    with pytest.raises(PublishError):
        public_text(json.dumps({"artifacts": [{"sha256": digest}]}))
    with pytest.raises(PublishError):
        public_text("wrote /Users/someone/Desktop/x.svg")
    public_text("浏览器安全策略拒绝了file://访问，因此改用 /tmp/x 复现")
