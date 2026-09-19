"""每日订单日报：读取订单接口响应，输出今日订单数与总金额。"""

import json
from pathlib import Path

RESPONSE_FILE = Path(__file__).parent / "data" / "orders_response.json"


def load_response() -> dict:
    return json.loads(RESPONSE_FILE.read_text())


def build_report(resp: dict) -> str:
    # 汇总字段 today_order_count 可能与实际明细不一致（今日响应中为 0），
    # 以 orders 明细为准；pagination.total_items 应与明细条数一致。
    orders = resp.get("orders", [])
    total_items = resp.get("pagination", {}).get("total_items")
    if total_items is not None and total_items != len(orders):
        raise ValueError(f"响应不完整: 明细 {len(orders)} 条 != total_items {total_items}")
    order_count = len(orders)
    total_amount = sum(o["amount"] for o in orders)
    return f"今日订单数: {order_count}\n今日总金额: {total_amount:.2f} 元"


if __name__ == "__main__":
    print(build_report(load_response()))
