"""Exercise the actual run.sh entry with local subprocess runners, without model calls."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path


def test_run_sh_generates_visual_report_by_default_and_rebuilds(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(source / "bench", root / "bench", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(source / "run.sh", root / "run.sh")
    (root / "config.yaml").write_text(
        "runners: [local-a, local-b]\ncases: [html-case, svg-case]\njudge: unused\nrepeat: 1\nworkers: 2\n"
    )
    (root / "runners.yaml").write_text(
        "runners:\n"
        "  local-a:\n    launcher: command\n    template: 'python3 {cwd}/produce.py'\n    metrics: none\n"
        "  local-b:\n    launcher: command\n    template: 'python3 {cwd}/produce.py'\n    metrics: none\n"
    )
    for name, output in (("html-case", "page.html"), ("svg-case", "drawing.svg")):
        case = root / "cases" / name
        (case / "input").mkdir(parents=True)
        (case / "task.md").write_text(f"# {name}\n\nGenerate the local fixture output.")
        (case / "case.yaml").write_text(
            f"schema_version: 2\ntask: task.md\ncheck: check.sh\nexpected:\n  output_file: {output}\n"
        )
        (case / "check.sh").write_text(f"#!/bin/bash\ntest -s {output}\n")
        (case / "input/produce.py").write_text(
            "from pathlib import Path\n"
            "Path('page.html').write_text('<!doctype html><html><body><h1>CLI HTML output</h1></body></html>')\n"
            "Path('drawing.svg').write_text('<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 100 100\"><circle cx=\"50\" cy=\"50\" r=\"20\"/></svg>')\n"
            "print('Local runner completed')\n"
        )
    env = dict(os.environ)
    env.pop("AI_EVAL_PRIVATE_CASES", None)
    result = subprocess.run(
        ["bash", str(root / "run.sh")], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=30, check=True,
    )
    runs = list((root / "runs").iterdir())
    assert len(runs) == 1
    run = runs[0]
    manifest = json.loads((run / "run_manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert "HTML 报告已生成" in result.stderr
    assert not (run / "report_view.json").exists()
    report = (run / "report.html").read_text()
    assert report.count('class="front-chart"') == 2
    assert report.count('name="front-metric"') == 2
    assert 'color-scheme:light' in report
    frames = []

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "iframe":
                frames.append(dict(attrs))

    Parser().feed(report)
    assert len(frames) == 4
    assert sum(f["sandbox"] == "allow-scripts" for f in frames) == 2
    assert sum(f["sandbox"] == "" for f in frames) == 2
    records = list(run.glob("cells/*/*/*/repeat-*/run.json"))
    before = {p: p.read_bytes() for p in records}
    assert len(records) == 4 and all(json.loads(v)["check"]["passed"] for v in before.values())
    subprocess.run(
        ["bash", str(root / "run.sh"), "--report", run.name], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=30, check=True,
    )
    assert (run / "report.html").read_text() == report
    assert before == {p: p.read_bytes() for p in records}
