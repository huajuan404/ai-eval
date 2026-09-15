from __future__ import annotations

import base64
import copy
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest

from bench.case import load_case
from bench.showcase import (
    ROOT,
    SITE,
    build,
    import_run,
    load_showcase,
    sha256,
    validate_svg,
)
from bench.showcase_svg import render_showcase


@pytest.fixture
def site(tmp_path):
    target = tmp_path / "docs"
    shutil.copytree(SITE, target)
    return target


def source_run(tmp_path: Path, data: dict, site: Path) -> Path:
    run = tmp_path / data["run_id"]
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({"status": "complete", "judge": data["judge"]}))
    for row in data["records"]:
        artifacts = run / Path(row["svg"]).parent
        artifacts.mkdir(parents=True)
        (run / row["svg"]).write_bytes((site / row["svg"]).read_bytes())
        (artifacts / "PROMPT.txt").write_text(load_case(ROOT / "cases" / row["case"]).task.prompt)
        (artifacts / "RUN_CONTEXT.json").write_text(row["context"])
        record = copy.deepcopy(row)
        record["human_note"] = "/Users/private-person/notes/private.txt"
        record["artifacts_dir"] = "/Users/private-person/private-output"
        record["unknown_private_field"] = "confidential local note"
        (artifacts.parent / "run.json").write_text(json.dumps(record))
        (artifacts.parent / "raw.txt").write_text("private launcher log")
        (artifacts / "OUTPUT.txt").write_text("private final response")
        (artifacts / "credentials.txt").write_text("private extra file")
    return run


def test_import_selects_public_fields_and_preserves_originals(tmp_path, site):
    data = load_showcase(site)
    run = source_run(tmp_path, data, site)
    destination = tmp_path / "export"
    import_run(run, destination)
    exported = load_showcase(destination)
    assert exported == data
    assert len([p for p in destination.rglob("*") if p.is_file()]) == 5
    assert "private-person" not in (destination / "showcase.json").read_text()
    for row in data["records"]:
        assert (destination / row["svg"]).read_bytes() == (site / row["svg"]).read_bytes()


def test_private_judge_text_rejects_import_before_writing(tmp_path, site):
    data = load_showcase(site)
    run = source_run(tmp_path, data, site)
    path = max(run.glob("cells/*/*/*/*/run.json"))
    record = json.loads(path.read_text())
    record["judge"]["reasoning"] += " read /Users/private-person/private-file.txt"
    path.write_text(json.dumps(record))
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="本机路径"):
        import_run(run, destination)
    assert not destination.exists()


@pytest.mark.parametrize("change", ["svg", "path", "score", "prompt"])
def test_changed_evidence_is_rejected(site, change):
    data = json.loads((site / "showcase.json").read_text())
    row = data["records"][0]
    if change == "svg":
        (site / row["svg"]).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    elif change == "path":
        row["svg"] = "../outside.svg"
    elif change == "score":
        row["judge"]["score"] = 999
    else:
        row["prompt_template_sha256"] = "0" * 64
    (site / "showcase.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_showcase(site)


@pytest.mark.parametrize("markup", [
    '<script>alert(1)</script>',
    '<circle onload="alert(1)"/>',
    '<use href="https://example.com/hidden.svg#shape"/>',
    '<rect fill="url(https://example.com/pixel)"/>',
    '<rect style="fill:u\\72l(https://example.com/pixel)"/>',
    '<foreignObject/>',
])
def test_public_svg_rejects_active_or_external_content(markup):
    with pytest.raises(ValueError):
        validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{markup}</svg>'.encode())


def test_import_rejects_svg_symlink(tmp_path, site):
    data = load_showcase(site)
    run = source_run(tmp_path, data, site)
    source = run / data["records"][0]["svg"]
    outside = tmp_path / "outside.svg"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match="符号链接"):
        import_run(run, destination)
    assert not destination.exists()


def animation_signature(root):
    return Counter((e.tag, tuple(sorted((k, v) for k, v in e.attrib.items()
                                        if k not in {"id", "href"})))
                   for e in root.iter() if e.tag.rsplit("}", 1)[-1].startswith("animate"))


@pytest.mark.parametrize("mobile", [False, True])
def test_composition_preserves_motion_and_resolves_every_fragment(site, mobile):
    data = load_showcase(site)
    hero = ET.fromstring(render_showcase(data, site, mobile=mobile))
    expected = Counter()
    for row in data["records"]:
        expected.update(animation_signature(ET.fromstring((site / row["svg"]).read_bytes())))
    assert animation_signature(hero) == expected
    ids = [e.get("id") for e in hero.iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    for e in hero.iter():
        for key, value in e.attrib.items():
            if key.rsplit("}", 1)[-1] == "href":
                assert value.startswith("#") and value[1:] in ids
            for ref in re.findall(r"url\(#([^)]*)\)", value):
                assert ref in ids


class PublicHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.images = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "iframe":
            self.frames.append(attrs["srcdoc"])
        if tag == "img":
            self.images.append(attrs["src"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))


def test_public_report_keeps_original_svg_and_has_no_private_or_dead_links(site):
    build(site)
    build(site, check=True)
    report = (site / "index.html").read_text()
    parsed = PublicHTML()
    parsed.feed(report)
    assert len(parsed.frames) == 4
    assert "raw.txt" not in report and "/Users/" not in report and "/tmp/" not in report
    assert 'id="front-score"' in report and 'id="front-time"' in report
    embedded = []
    for frame in parsed.frames:
        images = PublicHTML()
        images.feed(frame)
        embedded.extend(sha256(base64.b64decode(src.split(",", 1)[1])) for src in images.images)
    data = load_showcase(site)
    assert sorted(embedded) == sorted(r["svg_sha256"] for r in data["records"])
    for link in parsed.links:
        if link and not link.startswith(("#", "https://")):
            assert (site / link).is_file(), link
    (site / "assets/report.svg").write_text("outdated")
    with pytest.raises(ValueError, match="需要重建"):
        build(site, check=True)
