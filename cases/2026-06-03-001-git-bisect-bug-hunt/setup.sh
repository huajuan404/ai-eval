#!/bin/bash
# setup.sh — 一次性 clone textual 仓库到 input/repo/
# 跑 ./run.sh 前先跑这个。clone 大约 1-2 分钟。
# 之后 ./run.sh -c 2026-06-03-001-git-bisect-bug-hunt -r <runners>

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/input/repo"

if [ -d "$TARGET/.git" ]; then
    echo "✓ $TARGET 已存在（.git 在），跳过 clone"
    exit 0
fi

if [ -d "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
    echo "⚠️  $TARGET 存在但不是有效 repo，删除后重 clone"
    rm -rf "$TARGET"
fi

mkdir -p "$SCRIPT_DIR/input"
echo "→ 正在 clone Textualize/textual（全历史 ~1-2 分钟）..."
git clone https://github.com/Textualize/textual.git "$TARGET"

echo "→ checkout 374478a（v0.48.0 引入 stdin hang 的 commit）"
cd "$TARGET"
git checkout 374478a

echo "✓ 完成。input/repo 已经是 textual @ 374478a，git 历史可查。"
echo ""
echo "现在可以跑："
echo "  ./run.sh -c 2026-06-03-001-git-bisect-bug-hunt -r <runners>"
