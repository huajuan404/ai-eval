#!/bin/bash
#
# run.sh - bench 编排器薄入口（委托给 Python 包 bench）
#
# 用法:
#   ./run.sh                                 # 按 config.yaml 跑全部
#   ./run.sh -l                              # 列出可用用例与 runner 档案
#   ./run.sh -c <case>                       # 只跑指定用例
#   ./run.sh -c <case> -r codex,claude       # 指定用例与 runner（覆盖 config）
#   ./run.sh --repeat 3                      # 每格跑 3 次（中位数 + 离散度）
#   ./run.sh -w 1                            # 强制串行（调试/测试）
#   ./run.sh -q                              # 静默模式（只输出警告与汇总）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
exec python3 -m bench "$@"
