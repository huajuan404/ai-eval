#!/usr/bin/env bash
# 把 session-to-eval skill 软链进 Claude Code 的 skills 目录。
# 软链而非拷贝：源码随 ai-eval 仓库更新即时生效（单一事实源）。
#
# 用法：
#   bash install.sh
# 可用环境变量覆盖目标目录（测试用）：
#   CLAUDE_SKILLS_DIR=/tmp/skills bash install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="session-to-eval"
DEST_DIR="${CLAUDE_SKILLS_DIR:-${HOME}/.claude/skills}"
DEST="${DEST_DIR}/${SKILL_NAME}"

mkdir -p "${DEST_DIR}"

if [ -L "${DEST}" ]; then
  current="$(readlink "${DEST}")"
  if [ "${current}" = "${SRC}" ]; then
    echo "已是预期软链（幂等，无需操作）：${DEST} -> ${SRC}"
    exit 0
  fi
  echo "拒绝覆盖：${DEST} 已是软链但指向 ${current}，非本 skill 源 ${SRC}" >&2
  exit 1
fi

if [ -e "${DEST}" ]; then
  echo "拒绝覆盖：${DEST} 已存在且非软链，请先手动处理后重试" >&2
  exit 1
fi

ln -s "${SRC}" "${DEST}"
echo "已软链：${DEST} -> ${SRC}"
