#!/bin/bash
# 在 agent 产物目录运行修复后的日报脚本，校验数字面向真实数据：
#   订单数必须是 47（orders 数组长度），总金额必须是 12057.91。
# 只跑通不修字段映射（输出 0）→ FAIL。
set -u
out=$(python3 daily_report.py 2>&1) || { echo "FAIL: 脚本运行失败: $out"; exit 1; }
echo "$out"
echo "$out" | grep -q "47" || { echo "FAIL: 日报订单数不是 47（仍在读遗留 count 字段？）"; exit 1; }
echo "$out" | grep -q "12057.91" || { echo "FAIL: 总金额不是 12057.91（金额计算仍受错误 count 截断？）"; exit 1; }
echo "PASS: 日报数字与原始响应真实数据一致"
exit 0
