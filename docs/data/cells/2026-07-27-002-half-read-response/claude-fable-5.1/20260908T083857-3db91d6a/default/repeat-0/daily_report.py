"""每日订单日报：读取订单接口响应，输出今日订单数与总金额。"""

import json
import sys
from pathlib import Path

RESPONSE_FILE = Path(__file__).parent / "data" / "orders_response.json"


def load_response() -> dict:
    return json.loads(RESPONSE_FILE.read_text())


def build_report(resp: dict) -> str:
    # 以订单正文为准统计，不信任接口的汇总字段（曾出现 today_order_count=0 但正文有 47 单）。
    orders = resp.get("orders", [])
    order_count = len(orders)
    total_amount = sum(o["amount"] for o in orders)

    total_items = resp.get("pagination", {}).get("total_items")
    if total_items is not None and total_items != order_count:
        raise ValueError(
            f"响应不完整：pagination.total_items={total_items}，实际仅读到 {order_count} 单，需要翻页拉取"
        )
    if resp.get("today_order_count") != order_count:
        print(
            f"警告：接口 today_order_count={resp.get('today_order_count')} 与订单正文 {order_count} 单不一致，以正文为准",
            file=sys.stderr,
        )
    return f"今日订单数: {order_count}\n今日总金额: {total_amount:.2f} 元"


if __name__ == "__main__":
    print(build_report(load_response()))
