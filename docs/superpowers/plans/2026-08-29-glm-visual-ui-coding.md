# GLM Visual UI Coding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one public, runnable ai-eval case for the GLM-5.3-Flash official visual UI coding workflow.

**Architecture:** The runner sees only four generated screenshots and a faithful model-independent task. A hidden locked Next.js reference app owns the screenshots and doubles as the positive fixture. A shell check builds the contestant project, starts it, drives four browser paths, captures screenshots, computes SSIM, and writes an item-level check report; an LLM rubric grades fidelity above the deterministic floor.

**Tech Stack:** Python 3.11+, pytest, YAML schema v2, Bash, Node.js, Next.js, TypeScript, Playwright, ImageMagick.

---

### Task 1: Case contract tests

**Files:**
- Create: `tests/test_official_visual_ui_case.py`

- [ ] **Step 1: Write the failing contract test**

```python
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
    for phrase in ("Next.js", "TypeScript", "共享组件", "导航结构", "交互状态", "动效逻辑", "逐页截图"):
        assert phrase in prompt
    for leaked in ("评分", "check.sh", "SSIM", "Playwright", "通过阈值"):
        assert leaked not in prompt
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest tests/test_official_visual_ui_case.py -q`

Expected: FAIL because `cases/2026-08-29-001-visual-ui-coding/case.yaml` does not exist.

- [ ] **Step 3: Commit the RED test**

```bash
git add tests/test_official_visual_ui_case.py
git commit -m "test: define visual UI coding case contract"
```

### Task 2: Hidden reference application and generated inputs

**Files:**
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/package.json`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/package-lock.json`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/tsconfig.json`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/next-env.d.ts`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/app/layout.tsx`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/app/page.tsx`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/reference-app/app/globals.css`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/render_reference.mjs`
- Generate: `cases/2026-08-29-001-visual-ui-coding/input/reference/*.png`

- [ ] **Step 1: Create the minimal locked Next.js reference app**

Implement one client component whose route/query state renders exactly four states: `/`, `/runs?filter=open`, `/runs/run-4821`, and `/?menu=open`. Use one shared shell and fixed fixture data. Expose accessible names `Runs`, `Status filter`, `Run run-4821`, and `Open navigation` so browser validation tests behavior rather than CSS selectors.

- [ ] **Step 2: Install locked dependencies and build**

Run: `cd cases/2026-08-29-001-visual-ui-coding/oracle/reference-app && npm install && npm run build`

Expected: exit 0 and `.next/` created.

- [ ] **Step 3: Render all four inputs from the generator**

Run: `node cases/2026-08-29-001-visual-ui-coding/oracle/render_reference.mjs`

Expected: four non-empty PNG files with desktop dimensions `1440×1000` and mobile dimensions `390×844`.

- [ ] **Step 4: Inspect all four screenshots**

Open every PNG and verify shared visual language, readable text, open filter state, detail page, and mobile drawer. Fix only reference source and rerender.

### Task 3: Case definition and deterministic checker

**Files:**
- Create: `cases/2026-08-29-001-visual-ui-coding/case.yaml`
- Create: `cases/2026-08-29-001-visual-ui-coding/prompts/task.md`
- Create: `cases/2026-08-29-001-visual-ui-coding/prompts/rubric.md`
- Create: `cases/2026-08-29-001-visual-ui-coding/check.sh`
- Create: `cases/2026-08-29-001-visual-ui-coding/oracle/check_visual_ui.mjs`
- Create: `cases/2026-08-29-001-visual-ui-coding/README.md`

- [ ] **Step 1: Add the minimal schema v2 manifest**

```yaml
schema_version: 2
name: 2026-08-29-001-visual-ui-coding
class: coding
task: prompts/task.md
check:
  script: check.sh
  report_file: ai_eval_check_report.json
judge:
  rubric: prompts/rubric.md
  dimensions: [design_fidelity, page_system, interaction_states, responsive_behavior, engineering_validation]
expected:
  max_score: 25
  passing_threshold: 15
  source: https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash
```

- [ ] **Step 2: Add the faithful task and rubric**

The task keeps the official sequence and requirements but does not disclose output paths, selectors, check mechanics, dimensions, or thresholds. The rubric requires visual inspection of reference/actual PNGs and JSON-only judge output.

- [ ] **Step 3: Implement `check.sh` and browser validation**

The checker must reject zero/multiple project roots, missing dependencies, missing Next/React/TypeScript declarations, build failure, service startup failure, inaccessible interactions, missing screenshots, or insufficient SSIM. It writes schema v1 structured items for all four screens.

- [ ] **Step 4: Verify GREEN on contract tests**

Run: `python3 -m pytest tests/test_official_visual_ui_case.py -q`

Expected: all tests pass.

### Task 4: Adversarial verification

**Files:**
- Modify: `tests/test_official_visual_ui_case.py`

- [ ] **Step 1: Add checker integration tests around the reference fixture**

Add one slow test that copies the hidden reference app, installs from its lockfile when the cache is absent, runs `check.sh`, and asserts a valid report with four correct items. Add focused tests for manifest rejection without Next and checker failure for a white-theme mutation.

- [ ] **Step 2: Verify the positive fixture**

Run: `python3 -m pytest tests/test_official_visual_ui_case.py -q`

Expected: reference fixture passes, malformed fixture and white-theme mutation are rejected.

- [ ] **Step 3: Validate the case and run repository gates**

```bash
python3 .claude/skills/session-to-eval/scripts/validate_case.py cases/2026-08-29-001-visual-ui-coding
python3 -m pytest
python3 -m ruff check bench/ .claude/skills/session-to-eval/ tests/
git diff --check
```

Expected: valid and complete case; all tests pass; ruff and diff checks exit 0.

- [ ] **Step 4: Commit only the case and its tests**

```bash
git add cases/2026-08-29-001-visual-ui-coding tests/test_official_visual_ui_case.py
git commit -m "feat: add official visual UI coding eval case"
```
