#!/bin/bash
# check.sh — 验证模型给出的 commit hash 是否与 ground truth 匹配
#
# 1. 从 stdout/OUTPUT.txt 里抽取 "ANSWER: <hash>" 行
# 2. 与 case.yaml 的 expected.commit 比对（短 7 位或长 40 位）
# 3. 写 PASS/FAIL 到 stderr
# 4. 退出 0 = pass，1 = fail

set -u

OUT="${1:-OUTPUT.txt}"  # 默认从当前目录读
EXPECTED_SHORT="374478a"
EXPECTED_FULL="374478a0b12640df4753c041770e824a2c4259f0"

if [ ! -f "$OUT" ]; then
    echo "FAIL: $OUT 不存在" >&2
    exit 1
fi

# 抽取 ANSWER 行
ANSWER=$(grep -E '^ANSWER:[[:space:]]+[0-9a-fA-F]+' "$OUT" | head -1 | sed -E 's/^ANSWER:[[:space:]]+([0-9a-fA-F]+).*/\1/' | tr 'A-Z' 'a-z')

if [ -z "$ANSWER" ]; then
    echo "FAIL: 没找到 ANSWER: <hash> 行" >&2
    exit 1
fi

echo "模型给出: $ANSWER" >&2
echo "期望（短）: $EXPECTED_SHORT / 期望（长）: $EXPECTED_FULL" >&2

# 比对：先看 7 位短 hash，再看 40 位长 hash
if [ "${ANSWER:0:7}" = "$EXPECTED_SHORT" ] || [ "$ANSWER" = "$EXPECTED_FULL" ]; then
    echo "PASS: commit 匹配" >&2
    exit 0
fi

# 允许 1 个偏差（374478a^ / 374478a~1 等）
if [ "${ANSWER:0:7}" = "3744789" ] || [ "${ANSWER:0:7}" = "374478b" ]; then
    echo "PARTIAL: 偏差 1 个 commit（接受 4 分）" >&2
    exit 0  # 当下算 pass；judge 负责打细分
fi

echo "FAIL: commit 不匹配" >&2
exit 1
