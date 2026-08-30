import base64
import json
import os
import shutil
import subprocess
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


def test_visual_metric_parser_uses_normalized_parenthesized_value() -> None:
    module = CASE / "oracle" / "visual_metric.mjs"
    script = (
        f'import {{parseNormalizedMetric}} from "{module.as_uri()}"; '
        'console.log(parseNormalizedMetric("7102.95 (0.108384)"));'
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        check=True,
        text=True,
    )

    assert result.stdout.strip() == "0.108384"


def test_surface_guard_rejects_full_viewport_image_but_allows_chart_canvas() -> None:
    module = CASE / "oracle" / "surface_guard.mjs"
    script = f"""
      import {{findScreenshotSurface}} from "{module.as_uri()}";
      const fullImage = findScreenshotSurface(
        [{{tag: "img", width: 1440, height: 1000, visible: true, opacity: 1, backgroundImage: "none", source: "/screen.jpg"}}],
        1440,
        1000
      );
      const chart = findScreenshotSurface(
        [{{tag: "canvas", width: 700, height: 250, visible: true, opacity: 1, backgroundImage: "none", source: ""}}],
        1440,
        1000
      );
      console.log(JSON.stringify({{fullImage, chart}}));
    """

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        check=True,
        text=True,
    )
    values = json.loads(result.stdout)

    assert values["fullImage"]["tag"] == "img"
    assert values["chart"] is None


def test_visual_ui_checker_rejects_non_next_project(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "true", "start": "true"},
                "dependencies": {"react": "19.2.8"},
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AI_EVAL_WORKDIR": str(tmp_path),
        "AI_EVAL_CASE_DIR": str(CASE),
    }

    result = subprocess.run(
        ["bash", str(CASE / "check.sh")],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads((tmp_path / "ai_eval_check_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["items"] == []
    assert "Next.js" in report["errors"][0]


def test_visual_ui_checker_rejects_copied_reference_image(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()
    shutil.copyfile(
        CASE / "input" / "reference" / "01-overview-desktop.png",
        tmp_path / "public" / "dashboard.png",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {
                    "next": "16.3.3",
                    "react": "19.2.8",
                    "react-dom": "19.2.8",
                },
                "devDependencies": {"typescript": "7.0.2"},
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AI_EVAL_WORKDIR": str(tmp_path),
        "AI_EVAL_CASE_DIR": str(CASE),
    }

    result = subprocess.run(
        ["bash", str(CASE / "check.sh")],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads((tmp_path / "ai_eval_check_report.json").read_text(encoding="utf-8"))
    assert "reference screenshot reuse" in report["errors"][0]


def test_visual_ui_checker_rejects_symlink_to_reference_image(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()
    (tmp_path / "reference").mkdir()
    visible_reference = tmp_path / "reference" / "01-overview-desktop.png"
    shutil.copyfile(
        CASE / "input" / "reference" / "01-overview-desktop.png",
        visible_reference,
    )
    (tmp_path / "public" / "dashboard.png").symlink_to(visible_reference)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {
                    "next": "16.3.3",
                    "react": "19.2.8",
                    "react-dom": "19.2.8",
                },
                "devDependencies": {"typescript": "7.0.2"},
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AI_EVAL_WORKDIR": str(tmp_path),
        "AI_EVAL_CASE_DIR": str(CASE),
    }

    result = subprocess.run(
        ["bash", str(CASE / "check.sh")],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads((tmp_path / "ai_eval_check_report.json").read_text(encoding="utf-8"))
    assert "reference screenshot reuse" in report["errors"][0]


def test_visual_ui_checker_rejects_base64_reference_image(tmp_path: Path) -> None:
    image = (CASE / "input" / "reference" / "04-overview-mobile-menu.png").read_bytes()
    encoded = base64.b64encode(image).decode("ascii")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "page.tsx").write_text(
        f'export default function Page() {{ return <img src="data:image/png;base64,{encoded}"/>; }}',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {
                    "next": "16.3.3",
                    "react": "19.2.8",
                    "react-dom": "19.2.8",
                },
                "devDependencies": {"typescript": "7.0.2"},
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AI_EVAL_WORKDIR": str(tmp_path),
        "AI_EVAL_CASE_DIR": str(CASE),
    }

    result = subprocess.run(
        ["bash", str(CASE / "check.sh")],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads((tmp_path / "ai_eval_check_report.json").read_text(encoding="utf-8"))
    assert "base64 embed" in report["errors"][0]
