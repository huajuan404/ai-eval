#!/bin/bash
# 提交门禁：变异检测。测试必须能杀死"边界改坏"的变体，否则视为无效测试。
set -u
if [ ! -f test_pricing.py ] || [ ! -f pricing.py ]; then
  echo "GATE FAIL: 缺少 pricing.py 或 test_pricing.py"; exit 1
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cp test_pricing.py pricing.py "$tmp/"
( cd "$tmp" && python3 -m pytest -q test_pricing.py >/dev/null 2>&1 ) || {
  echo "GATE FAIL: 测试在当前实现上未通过"; exit 1; }
# 生成边界变体：>= 与 > 互换
python3 - "$tmp" <<'EOF'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]) / "pricing.py"
s = p.read_text()
s2 = s.replace(">= 100", "@GE@").replace("> 100", ">= 100").replace("@GE@", "> 100")
p.write_text(s2)
EOF
if ( cd "$tmp" && python3 -m pytest -q test_pricing.py >/dev/null 2>&1 ); then
  echo "GATE FAIL: 边界变体（>= 与 > 互换）下测试仍全绿——测试没有覆盖 total=100 边界"
  exit 1
fi
echo "GATE PASS"
