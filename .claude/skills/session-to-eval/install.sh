#!/usr/bin/env bash
# 把 session-to-eval skill 软链进本机 skills 目录。
# 软链而非拷贝：源码随 ai-eval 仓库更新即时生效（单一事实源），config.toml 也随软链生效。
#
# 用法：
#   bash install.sh                       # 默认装进 Claude Code / Codex / .agents 三端
# 显式单目标（测试 / 自定义）：
#   CLAUDE_SKILLS_DIR=/tmp/skills bash install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="session-to-eval"

link_into() {
  local dest_dir="$1"
  local dest="${dest_dir}/${SKILL_NAME}"
  mkdir -p "${dest_dir}"
  if [ -L "${dest}" ]; then
    local current
    current="$(readlink "${dest}")"
    if [ "${current}" = "${SRC}" ]; then
      echo "已是预期软链（幂等，无需操作）：${dest} -> ${SRC}"
      return 0
    fi
    echo "拒绝覆盖：${dest} 已是软链但指向 ${current}，非本 skill 源 ${SRC}" >&2
    return 1
  fi
  if [ -e "${dest}" ]; then
    echo "拒绝覆盖：${dest} 已存在且非软链，请先手动处理后重试" >&2
    return 1
  fi
  ln -s "${SRC}" "${dest}"
  echo "已软链：${dest} -> ${SRC}"
}

# 显式覆盖：只装到指定目录（测试 / 单目标）。
if [ -n "${CLAUDE_SKILLS_DIR:-}" ]; then
  link_into "${CLAUDE_SKILLS_DIR}"
  exit 0
fi

# 默认：装进 Claude Code / Codex / .agents 三端 skills 目录（任一失败不阻断其余）。
rc=0
link_into "${HOME}/.claude/skills" || rc=1
link_into "${HOME}/.codex/skills" || rc=1
link_into "${HOME}/.agents/skills" || rc=1
exit "${rc}"
