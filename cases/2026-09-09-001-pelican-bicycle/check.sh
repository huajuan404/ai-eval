#!/usr/bin/env bash
set -euo pipefail
python3 "${AI_EVAL_CASE_DIR:?}/check.py"
