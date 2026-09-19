"""站点生成：每个数字来自账本，链接不死，页面不含本机路径，产物漂移能被 --check 抓住。"""

from __future__ import annotations

import json
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from bench.models import ModelBook
from bench.publish import sha256
from bench.site import SITE, SiteError, Verdicts, build, load_ledger, verdicts


@pytest.fixture
def site(tmp_path):
    target = tmp_path / "docs"
    shutil.copytree(SITE, target)
    return target


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []
        self.frames = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.hrefs.append(attributes["href"])
        if tag == "iframe":
            self.frames += 1


def test_build_is_reproducible_and_check_detects_drift(site):
    files = build(site)
    assert Path("index.html") in files and Path("data/index.json") in files
    build(site, check=True)
    (site / "index.html").write_text("outdated")
    with pytest.raises(SiteError, match="需要重建"):
        build(site, check=True)


def test_every_case_and_model_page_exists_with_live_links(site):
    build(site)
    ledger = load_ledger(site)
    for info in ledger.cases.values():
        page = site / "cases" / info.id / "index.html"
        assert page.is_file()
        text = page.read_text(encoding="utf-8")
        assert "/Users/" not in text and "raw.txt" not in text
        parser = Links()
        parser.feed(text)
        for href in parser.hrefs:
            if href.startswith(("http", "#")):
                continue
            assert (page.parent / href).resolve().exists(), (info.id, href)
    for runner in {r["runner_label"] for r in ledger.records}:
        assert (site / "models" / runner / "index.html").is_file()


def test_case_page_numbers_come_from_ledger(site):
    build(site)
    ledger = load_ledger(site)
    case = "2026-09-09-001-pelican-bicycle"
    page = (site / "cases" / case / "index.html").read_text(encoding="utf-8")
    for (name, runner), rows in ledger.latest.items():
        if name != case:
            continue
        judge = rows[0]["judge"]
        assert f"{judge['score']:g} / {judge['max']:g}" in page, runner
        assert ledger.runner_name(runner) in page
    parser = Links()
    parser.feed(page)
    assert parser.frames == sum(1 for (name, _) in ledger.latest if name == case)


def test_index_lists_core_cases_first_and_index_json_matches(site):
    build(site)
    ledger = load_ledger(site)
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "核心集" in index
    data = json.loads((site / "data" / "index.json").read_text(encoding="utf-8"))
    assert {c["id"] for c in data["cases"]} == set(ledger.cases)
    assert len(data["latest"]) == len(ledger.latest)
    for cell in data["latest"]:
        rows = ledger.latest[(cell["case"], cell["runner"])]
        assert cell["verdict"] == verdicts(rows).label


def test_tampered_artifact_fails_ledger_integrity(site):
    ledger = load_ledger(site)
    artifact = next(a for r in ledger.records for a in r["artifacts"] if a["path"].endswith(".svg"))
    (site / artifact["path"]).write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>')
    with pytest.raises(SiteError, match="哈希不符"):
        load_ledger(site)


def test_hero_svg_is_driven_by_featured_and_keeps_originals(site):
    build(site)
    ledger = load_ledger(site)
    hero = (site / "assets" / "report.svg").read_text(encoding="utf-8")
    featured = json.loads((site / "data" / "featured.json").read_text())
    for runner in featured["runners"]:
        assert ledger.runner_name(runner) in hero
    for case in featured["cases"]:
        assert (case.get("title") or ledger.cases[case["id"]].title) in hero
    animated = len(re.findall(r"<(?:svg:)?animate", hero))
    assert animated > 0
    # 首图指定的四格缺一不可
    featured["runners"] = ["kimi-k3", "no-such-runner"]
    (site / "data" / "featured.json").write_text(json.dumps(featured))
    with pytest.raises(SiteError, match="不完整"):
        build(site)


def test_verdict_summary_labels():
    assert Verdicts(1, 0, 0).label == "通过" and Verdicts(0, 1, 0).label == "未通过"
    assert Verdicts(0, 0, 1).label == "未评" and Verdicts(0, 0, 1).klass == "na"
    assert Verdicts(2, 1, 0).label == "2/3 通过" and Verdicts(2, 1, 0).klass == "warn"


def test_models_without_pricing_show_dash_not_zero(site):
    build(site, book=ModelBook())
    page = (site / "cases" / "2026-09-09-001-pelican-bicycle" / "index.html").read_text(encoding="utf-8")
    assert "$0.00" not in page
