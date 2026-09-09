#!/usr/bin/env bash
set -euo pipefail
uv run --no-project --no-config --python 3.12 --script "${AI_EVAL_CASE_DIR:?}/check.py"
