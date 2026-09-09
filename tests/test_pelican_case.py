from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bench.case import load_case

CASE_DIR = Path(__file__).resolve().parents[1] / "cases/2026-09-09-001-pelican-bicycle"


def _checker():
    spec = importlib.util.spec_from_file_location("pelican_check", CASE_DIR / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pelican_is_a_scoped_visual_diagnostic():
    case = load_case(CASE_DIR)
    assert case.schema_version == 2
    assert case.evaluation.generalizes is False
    assert case.expected["output_file"] == "pelican.svg"
    assert "Generate an SVG of a pelican riding a bicycle." in case.task.prompt
    assert 'render.png' in case.judge.rubric and 'score: null' in case.judge.rubric


def test_svg_format_gate_does_not_pretend_to_recognize_a_pelican():
    _checker().validate('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle r="10"/></svg>')


@pytest.mark.parametrize("content", [
    '<svg/>',
    '<svg xmlns="http://www.w3.org/2000/svg"/>',
    '<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///etc/passwd">]><svg/>',
    '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><circle onload="alert(1)"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AAAA"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><use href="https://example.com/a.svg#x"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://example.com/a)"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 nan 100"><rect/></svg>',
])
def test_svg_gate_rejects_active_external_or_invalid_content(content):
    with pytest.raises(ValueError):
        _checker().validate(content)


def test_svg_internal_gradients_are_allowed():
    _checker().validate('<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="a"/></defs><rect width="10" height="10" fill="url(#a)"/></svg>')
