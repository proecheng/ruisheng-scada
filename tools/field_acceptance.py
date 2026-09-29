"""Summarize saved browser evidence without treating it as physical-bus proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("evidence timestamps must include a timezone")
    return result


def summarize_intervals(values: list[str], period: float = 5.0) -> dict[str, Any]:
    if not math.isfinite(period) or period <= 0:
        raise ValueError("period must be positive and finite")
    times = sorted({timestamp(value) for value in values})
    gaps = [(right - left).total_seconds() for left, right in zip(times, times[1:], strict=False)]
    missed = sum(max(0, math.floor(gap / period + 0.5) - 1) for gap in gaps)
    long_gaps = [
        {"from": left.isoformat(), "to": right.isoformat(), "seconds": round(gap, 6)}
        for left, right, gap in zip(times, times[1:], gaps, strict=False)
        if gap > period * 1.5
    ]
    return {
        "observed_rounds": len(times),
        "first": times[0].isoformat() if times else None,
        "last": times[-1].isoformat() if times else None,
        "span_seconds": (times[-1] - times[0]).total_seconds() if times else 0,
        "configured_period_seconds": period,
        "median_interval_seconds": median(gaps) if gaps else None,
        "p95_interval_seconds": sorted(gaps)[math.ceil(len(gaps) * 0.95) - 1] if gaps else None,
        "max_interval_seconds": max(gaps) if gaps else None,
        "long_gap_threshold_seconds": period * 1.5,
        "long_gap_count": len(long_gaps),
        "estimated_unobserved_rounds_between_samples": missed,
        "estimated_observed_fraction": len(times) / (len(times) + missed) if times else None,
        "long_gaps": long_gaps,
        "method": "unique timestamps; nearest-period estimate between first/last sample only",
    }


def summarize_browser(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    evidence = json.loads(raw.decode("utf-8-sig"))
    samples = evidence.get("samples", [])
    if not samples:
        raise ValueError("browser evidence has no bounded observation samples")
    start = min(timestamp(sample["at"]) for sample in samples)
    end = max(timestamp(sample["at"]) for sample in samples)
    rounds = evidence.get("rounds")
    if isinstance(rounds, dict):
        values = list(rounds)
        unique_point_count_verified = False  # Two tabs can report each point twice.
    else:
        frames = [
            frame
            for frame in evidence.get("frames", [])
            if frame.get("dev_number") == "DEV001" and frame.get("ts")
        ]
        values = [frame["ts"] for frame in frames]
        unique_point_count_verified = True
    bounded = [value for value in values if start <= timestamp(value) <= end]
    result = summarize_intervals(bounded)
    if unique_point_count_verified:
        expected = 38
        by_time: dict[datetime, set[int]] = {}
        for frame in frames:
            at = timestamp(frame["ts"])
            if start <= at <= end:
                by_time.setdefault(at, set()).add(frame["point_id"])
        result["rounds_with_38_unique_point_ids"] = sum(
            len(points) == expected for points in by_time.values()
        )
    result.update(
        source=str(path),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        evidence_kind="browser_observation_not_database_completeness",
        observation_start=start.isoformat(),
        observation_end=end.isoformat(),
        point_identity_available=unique_point_count_verified,
        limitation="Transport/reconnection gaps cannot all be attributed to physical timeouts.",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {"schema_version": 1, "observations": [summarize_browser(p) for p in args.evidence]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
