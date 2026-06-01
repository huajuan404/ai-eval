#!/bin/bash
#
# run.sh - 案例运行脚本
# 用法:
#   ./run.sh              # 运行全部案例
#   ./run.sh [案例名]     # 运行指定案例

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
CLAUDE_SCRIPT="${HOME}/tools/c"
OUTPUT_BASE="${SCRIPT_DIR}/cases"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

die() {
    echo -e "${RED}错误: $1${NC}" >&2
    exit 1
}

show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -c, --case <案例名>   运行指定案例（默认全部）"
    echo "  -m, --model <编号>    只跑指定模型（默认 config.yaml 中的全部）"
    echo "  -l, --list            列出所有案例"
    echo "  -h, --help            显示帮助"
}

# 列出所有案例
list_cases() {
    echo "可用案例:"
    for case_dir in "${OUTPUT_BASE}"/*/; do
        [[ -d "$case_dir" ]] || continue
        local name=$(basename "$case_dir")
        local readme="${case_dir}README.md"
        local desc=""
        if [[ -f "$readme" ]]; then
            desc=$(head -3 "$readme" | tail -1 | sed 's/^# //')
        fi
        echo -e "  ${GREEN}${name}${NC}  ${desc}"
    done
}

# 加载配置
load_config() {
    [[ -f "$CONFIG_FILE" ]] || die "配置文件不存在: $CONFIG_FILE"
    # TODO: 解析 YAML 配置
    echo -e "${YELLOW}配置加载功能待实现${NC}"
}

# 运行单个案例
run_case() {
    local case_name="$1"
    local case_dir="${OUTPUT_BASE}/${case_name}"

    [[ -d "$case_dir" ]] || die "案例不存在: $case_name"

    local readme="${case_dir}/README.md"
    [[ -f "$readme" ]] || die "案例缺少 README.md: $case_name"

    echo -e "${GREEN}开始运行案例: ${case_name}${NC}"
    cat "$readme"
}

main() {
    local target_case=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -c|--case)
                target_case="$2"
                shift 2
                ;;
            -m|--model)
                # TODO: 支持只跑指定模型
                shift 2
                ;;
            -l|--list)
                list_cases
                exit 0
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                die "未知选项: $1"
                ;;
        esac
    done

    if [[ -n "$target_case" ]]; then
        run_case "$target_case"
    else
        echo "请使用 -l 查看可用案例"
    fi
}

main "$@"
