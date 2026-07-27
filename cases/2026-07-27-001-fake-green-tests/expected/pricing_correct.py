"""订单折扣计算。

规则（产品规格）：
- 订单总额满 100 元（含 100）打 9 折；
- 不满 100 元不打折；
- 折后金额保留两位小数。
"""


def calculate_discount(total: float) -> float:
    if total >= 100:
        return round(total * 0.9, 2)
    return round(total, 2)
