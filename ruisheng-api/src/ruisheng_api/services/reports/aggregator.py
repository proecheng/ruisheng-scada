"""每日聚合：输入 history rows → {dev: {point_id: {count, min, max, avg}}}。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def aggregate_daily(rows: list[dict[str, Any]]) -> dict[str, dict[int, dict[str, Any]]]:
    """
    Input rows: [{dev_number, point_id, rt_value, recorded_at}, ...]
    Output: {dev_number: {point_id: {count, min, max, avg}}}
    """
    buckets: dict[str, dict[int, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {"count": 0, "min": float("inf"), "max": float("-inf"), "sum": 0.0}
        )
    )
    for r in rows:
        v = float(r["rt_value"])
        b = buckets[str(r["dev_number"])][int(r["point_id"])]
        b["count"] = int(b["count"]) + 1
        b["min"] = min(float(b["min"]), v)
        b["max"] = max(float(b["max"]), v)
        b["sum"] = float(b["sum"]) + v
    result: dict[str, dict[int, dict[str, Any]]] = {}
    for dev, pts in buckets.items():
        result[dev] = {}
        for pid, b in pts.items():
            cnt = int(b["count"])
            result[dev][pid] = {
                "count": cnt,
                "min": float(b["min"]) if cnt else None,
                "max": float(b["max"]) if cnt else None,
                "avg": float(b["sum"]) / cnt if cnt else None,
            }
    return result
