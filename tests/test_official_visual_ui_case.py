from pathlib import Path

from bench.case import load_case


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "2026-08-29-001-visual-ui-coding"


def test_visual_ui_case_contract() -> None:
    case = load_case(CASE)

    assert case.schema_version == 2
    assert case.class_ == "coding"
    assert case.check.type == "script"
    assert case.check.report_file == "ai_eval_check_report.json"
    assert case.judge.dimensions == (
        "design_fidelity",
        "page_system",
        "interaction_states",
        "responsive_behavior",
        "engineering_validation",
    )


def test_visual_ui_case_exposes_only_four_png_inputs() -> None:
    exposed = sorted(
        path.relative_to(CASE / "input").as_posix()
        for path in (CASE / "input").rglob("*")
        if path.is_file()
    )

    assert exposed == [
        "reference/01-overview-desktop.png",
        "reference/02-runs-filter-desktop.png",
        "reference/03-run-detail-desktop.png",
        "reference/04-overview-mobile-menu.png",
    ]


def test_visual_ui_prompt_keeps_official_task_scope_without_eval_leaks() -> None:
    prompt = (CASE / "prompts" / "task.md").read_text(encoding="utf-8")

    for phrase in (
        "Next.js",
        "TypeScript",
        "共享组件",
        "导航结构",
        "交互状态",
        "动效逻辑",
        "逐页截图",
    ):
        assert phrase in prompt
    for leaked in ("评分", "check.sh", "SSIM", "Playwright", "通过阈值"):
        assert leaked not in prompt
