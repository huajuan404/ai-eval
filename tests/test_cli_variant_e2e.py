from __future__ import annotations

import textwrap
from pathlib import Path

from bench import __main__ as bench_cli

def test_real_cli_private_variant_fake_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    public_root = tmp_path / "public"
    public_root.mkdir()
    (public_root / "runners.yaml").write_text(
        textwrap.dedent(
            """
            runners:
              fake-direct:
                launcher: command
                template: "python3 {cwd}/fake_provider.py"
                model: fake-model
                metrics: none
            """
        ),
        encoding="utf-8",
    )
    (public_root / "config.yaml").write_text(
        "runners: []\ncases: []\njudge: fake-direct\nrepeat: 1\n",
        encoding="utf-8",
    )
    private_root = tmp_path / "private"
    case_dir = private_root / "cases" / "variant-e2e"
    input_dir = case_dir / "input"
    prompts = case_dir / "prompts"
    oracle = case_dir / "oracle"
    input_dir.mkdir(parents=True)
    prompts.mkdir()
    oracle.mkdir()

    (prompts / "original.md").write_text("original prompt", encoding="utf-8")
    (prompts / "v4.md").write_text("candidate prompt", encoding="utf-8")
    (oracle / "labels.jsonl").write_text('{"id":"1","expected":"false"}\n', encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: variant-e2e
            requires_engine: command
            task:
              type: custom
              variants:
                original:
                  prompt_file: prompts/original.md
                v4:
                  prompt_file: prompts/v4.md
              default_variant: v4
            run_contract:
              request_manifest_file: request_manifest.json
              request_manifest_required: true
            check:
              type: script
              script: check.sh
              report_file: evaluation.json
              protected_files: [oracle/labels.jsonl]
            expected:
              comparison:
                baseline_variant: original
                candidate_variant: v4
            evaluation:
              role: regression
              generalization_evidence: false
              unit_of_analysis: item
              independent_unit: item
            """
        ),
        encoding="utf-8",
    )
    (input_dir / "fake_provider.py").write_text(
        textwrap.dedent(
            """
            import hashlib
            import json
            from pathlib import Path

            cwd = Path.cwd()
            prompt = (cwd / "PROMPT.txt").read_text(encoding="utf-8")
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            manifest = {
                "schema_version": 1,
                "provider": {
                    "endpoint": "https://fake.invalid/anthropic",
                    "requested_model": "fake-model",
                    "api_path": "/v1/messages",
                    "api_version": "2023-06-01",
                    "temperature": 0,
                    "max_tokens": 100,
                    "credentials_present": True,
                },
                "wrapper_sha256": "fake-wrapper-v1",
                "requests": [{
                    "batch_id": "only",
                    "input_sha256": "fixture-input",
                    "request_prompt_sha256": prompt_hash,
                    "status_code": 200,
                    "auth_succeeded": True,
                    "returned_model": "fake-model",
                    "request_id": "fixture-request",
                }],
            }
            (cwd / "request_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            print("provider complete")
            """
        ),
        encoding="utf-8",
    )
    (case_dir / "check.py").write_text(
        textwrap.dedent(
            """
            import json
            import sys
            from pathlib import Path

            cwd = Path(sys.argv[1])
            prompt = (cwd / "PROMPT.txt").read_text(encoding="utf-8")
            correct = "candidate" in prompt
            report = {
                "schema_version": 1,
                "passed": correct,
                "summary": {"parse_ok": True},
                "items": [{
                    "id": "1",
                    "expected": "false",
                    "actual": "false" if correct else "true",
                    "correct": correct,
                    "slices": {"product": "fixture"},
                }],
                "errors": [] if correct else ["fixture false positive"],
            }
            (cwd / "evaluation.json").write_text(json.dumps(report), encoding="utf-8")
            raise SystemExit(0 if correct else 1)
            """
        ),
        encoding="utf-8",
    )
    (case_dir / "check.sh").write_text(
        '#!/bin/sh\npython3 "$(dirname "$0")/check.py" "$PWD"\n', encoding="utf-8"
    )
    monkeypatch.setattr(bench_cli, "ROOT", public_root)
    monkeypatch.setattr(bench_cli, "configure_log", lambda quiet=False: None)
    monkeypatch.setenv("AI_EVAL_PRIVATE_CASES", str(private_root / "cases"))
    exit_code = bench_cli.main(
        [
            "-c",
            "variant-e2e",
            "-r",
            "fake-direct",
            "--variants",
            "original,v4",
            "--repeat",
            "2",
            "-w",
            "1",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + "\n" + captured.err
    scorecards = list((private_root / "scorecards").glob("*.md"))
    assert len(scorecards) == 1
    markdown = scorecards[0].read_text(encoding="utf-8")
    assert "strict fixed=1，regressed=0" in markdown
    run_id = scorecards[0].stem
    assert (private_root / "runs" / run_id / "run_manifest.json").is_file()
    assert not (public_root / "scorecards" / f"{run_id}.md").exists()
