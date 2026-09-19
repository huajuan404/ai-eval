"""每日订单日报：读取订单接口响应，输出今日订单数与总金额。"""

import json
from pathlib import Path

RESPONSE_FILE = Path(__file__).parent / "data" / "orders_response.json"


def load_response() -> dict:
    return json.loads(RESPONSE_FILE.read_text())


def build_report(resp: dict) -> str:
    # today_order_count 不可信（实测为 0 而响应含 47 条订单），以 orders 明细为准。
    orders = resp.get("orders", [])
    total_items = (resp.get("pagination") or {}).get("total_items", len(orders))
    if total_items != len(orders):
        raise ValueError(
            f"响应不完整：共 {total_items} 条订单，本次只返回 {len(orders)} 条，需翻页后再统计"
        )
    total_amount = sum(o["amount"] for o in orders)
    return f"今日订单数: {len(orders)}\n今日总金额: {total_amount:.2f} 元"


if __name__ == "__main__":
    print(build_report(load_response()))
