"""每日订单日报：读取订单接口响应，输出今日订单数与总金额。"""

import json
from pathlib import Path

RESPONSE_FILE = Path(__file__).parent / "data" / "orders_response.json"


def load_response() -> dict:
    return json.loads(RESPONSE_FILE.read_text())


def build_report(resp: dict) -> str:
    order_count = resp["count"]
    total_amount = sum(o["amount"] for o in resp.get("orders", [])[:order_count])
    return f"今日订单数: {order_count}\n今日总金额: {total_amount:.2f} 元"


if __name__ == "__main__":
    print(build_report(load_response()))
