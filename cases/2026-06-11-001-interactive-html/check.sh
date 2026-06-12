#!/usr/bin/env bash
# check.sh — interactive-html 确定性闸门（精简版）。
# 只守硬约束：产物存在、有效 HTML、内容保真、单文件、响应式声明。
# 审美、动效、交互、图表——全部交给 LLM 裁判，不在此预设天花板。
set -u

FAIL=0
note() { printf '%s %s\n' "$1" "$2"; }
assert() { # assert <描述> <命令...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then note "PASS" "$desc"; else note "FAIL" "$desc"; FAIL=1; fi
}

HTML="docs/article.html"

# 1. 产物存在且非空
assert "HTML 文件存在且非空" test -s "$HTML"

# 2. 有效 HTML 结构
assert "包含 <!DOCTYPE html>" grep -qi '<!DOCTYPE html' "$HTML"
assert "包含 <html>" grep -q '<html' "$HTML"
assert "包含 <head>" grep -q '<head' "$HTML"
assert "包含 <body>" grep -q '<body' "$HTML"

# 3. 响应式声明
assert "包含 viewport meta 标签" grep -q 'name="viewport"' "$HTML"

# 4. 内容保真：源文关键标识不得丢失
for token in 'JIT' 'dogfood' 'Claude Cowork' 'PR cycle time'; do
  assert "内容保真: $token" grep -qF "$token" "$HTML"
done

# 5. 单文件约束：未创建外部 CSS/JS 文件（input 目录除外）
EXTERNAL_CSS=$(find . -name '*.css' -not -path './input/*' -not -path './.playwright-mcp/*' 2>/dev/null | wc -l)
EXTERNAL_JS=$(find . -name '*.js' -not -path './input/*' -not -path '*/node_modules/*' -not -path './.playwright-mcp/*' 2>/dev/null | wc -l)
if [ "$EXTERNAL_CSS" -eq 0 ] && [ "$EXTERNAL_JS" -eq 0 ]; then
  note "PASS" "无外部 CSS/JS 文件（单文件约束满足）"
else
  note "FAIL" "发现外部文件: ${EXTERNAL_CSS} CSS + ${EXTERNAL_JS} JS（违反单文件约束）"
  FAIL=1
fi

# 汇总
if [ "$FAIL" -eq 0 ]; then
  echo "--- 全部通过 ---"
  exit 0
else
  echo "--- 存在失败 ---"
  exit 1
fi
