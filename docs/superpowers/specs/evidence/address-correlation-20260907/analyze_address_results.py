"""Offline comparison of authenticated raw observations; never qualifies points."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

PACKAGE = Path("C:/ProgramData/Ruisheng/publisher-output/deploy-20260907.2")
sys.path.insert(0, str(PACKAGE))
import probe_modbus_rtu as probe  # noqa: E402

EVIDENCE = Path(__file__).parent


def compare(
    before: list[int], segment: list[int], after: list[int], offset: int, start: int
) -> dict:
    if len(before) != len(after) or offset < 0 or offset + len(segment) > len(before):
        raise ValueError("Invalid comparison geometry")
    rows = []
    for index, value in enumerate(segment):
        expected_before, expected_after = before[offset + index], after[offset + index]
        prefix_before, prefix_after = before[index], after[index]
        if expected_before != expected_after:
            status = "DYNAMIC_REFERENCE"
        elif value != expected_before:
            status = "MISMATCH"
        elif prefix_before == prefix_after == value:
            status = "MATCH_UNDISCRIMINATING"
        else:
            status = "MATCH_DISCRIMINATING"
        rows.append(
            {
                "request_address": start + index,
                "reference_before": expected_before,
                "subread_value": value,
                "reference_after": expected_after,
                "unshifted_prefix_before": prefix_before,
                "unshifted_prefix_after": prefix_after,
                "status": status,
                "stable_prefix_support": status == "MISMATCH"
                and prefix_before == prefix_after == value,
            }
        )
    counts = dict(Counter(row["status"] for row in rows))
    return {
        "rows": rows,
        "counts": counts,
        "stable_prefix_support_count": sum(row["stable_prefix_support"] for row in rows),
        "status": "ADDRESS_CORRELATION_INCONSISTENT"
        if counts.get("MISMATCH", 0)
        else (
            "INCONCLUSIVE"
            if counts.get("DYNAMIC_REFERENCE", 0) or not counts.get("MATCH_DISCRIMINATING", 0)
            else "CONSISTENT_IN_THIS_OBSERVATION_ONLY"
        ),
    }


def main() -> None:
    config = probe.load_config(EVIDENCE / "approved-config.json")
    path = EVIDENCE / "physical.jsonl"
    if path.stat().st_size > 128 * 1024:
        raise ValueError("Unexpected audit size")
    raw = path.read_bytes()
    events = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines()]
    start, end = events[0], events[-1]
    receipt = json.loads((EVIDENCE / "installed-receipt.json").read_text(encoding="utf-8-sig"))
    if start["event"] != "run_started" or end["event"] != "completed" or end["result"] != "valid":
        raise ValueError("Physical run is incomplete; do not infer address semantics")
    if (
        start["plan"] != probe.normalized_plan(config)
        or start["approval_scope"] != config.approval.scope_id
    ):
        raise ValueError("Approved physical plan mismatch")
    if (
        start["script_sha256"] != receipt["probe_sha256"]
        or start["image_id"] != receipt["gw_image_id"]
    ):
        raise ValueError("Installed script/image binding mismatch")
    if (
        start["receipt_sha256"]
        != hashlib.sha256((EVIDENCE / "installed-receipt.json").read_bytes()).hexdigest()
    ):
        raise ValueError("Installed receipt byte hash mismatch")
    if (
        start["script_sha256"]
        != hashlib.sha256((PACKAGE / "probe_modbus_rtu.py").read_bytes()).hexdigest()
    ):
        raise ValueError("Signed analyzer codec source binding mismatch")
    if (
        start["config_sha256"]
        != hashlib.sha256((EVIDENCE / "approved-config.json").read_bytes()).hexdigest()
    ):
        raise ValueError("Approved config hash mismatch")
    if any(event["run_id"] != start["run_id"] for event in events):
        raise ValueError("Mixed audit runs")
    requests = [event for event in events if event["event"] == "request_tx"]
    responses = [event for event in events if event["event"] == "response_rx"]
    if not (6 <= len(requests) <= 12) or end["completed_tx_count"] != len(requests):
        raise ValueError("Unexpected transmission count")
    if not end["tx_count_known"] or end["attempted_write_bytes"] != len(requests) * 8:
        raise ValueError("Incomplete transmission accounting")
    if len(responses) != len(requests):
        raise ValueError("Unpaired physical response")
    values = {}
    frames = []
    for number, (tx, rx) in enumerate(zip(requests, responses, strict=True), 1):
        index = tx["request_index"]
        expected = config.requests[index]
        for key in ("request_index", "attempt", "tx_number"):
            if tx[key] != rx[key]:
                raise ValueError("TX/RX pair mismatch")
        if (
            tx["tx_number"] != number
            or tx["start_address"] != expected.start_address
            or tx["register_count"] != expected.register_count
        ):
            raise ValueError("Request address/quantity mismatch")
        if (
            tx["tx_hex"]
            != probe.request_frame(1, 3, expected, scope_id=config.approval.scope_id).hex()
        ):
            raise ValueError("Request wire frame mismatch")
        checked = probe.classify_response(bytes.fromhex(rx["rx_hex"]), expected, 1, 3)
        if (
            checked["classification"] != rx["classification"]
            or checked["crc_valid"] != rx["crc_valid"]
        ):
            raise ValueError("Response classification mismatch")
        if checked["classification"] == "valid":
            if checked["registers"] != rx["registers"] or index in values:
                raise ValueError("Decoded values or successful request uniqueness mismatch")
            values[index] = checked["registers"]
            frames.append(
                {
                    "step": index + 1,
                    "start": expected.start_address,
                    "count": expected.register_count,
                    "tx_hex": tx["tx_hex"],
                    "rx_hex": rx["rx_hex"],
                    "registers": checked["registers"],
                    "tx_at": tx["timestamp"],
                    "rx_at": rx["timestamp"],
                    "latency_ms": rx["latency_ms"],
                }
            )
    if list(values) != list(range(6)):
        raise ValueError("Incomplete or reordered six-step run")
    groups = [
        compare(values[0], values[1], values[2], 6, 6),
        compare(values[3], values[4], values[5], 9, 27),
    ]
    output = {
        "run_id": start["run_id"],
        "started_at": start["timestamp"],
        "completed_at": end["timestamp"],
        "audit_sha256": hashlib.sha256(raw).hexdigest(),
        "completed_tx": len(requests),
        "retries": len(requests) - 6,
        "device_write_functions_sent": 0,
        "frames": frames,
        "groups": groups,
        "formal_acceptance": "BLOCKED",
        "physical_meanings_confirmed": False,
        "limitation": "Bracketing equality does not exclude intermediate transients; this is not a firmware diagnosis or calibration.",
    }
    (EVIDENCE / "address-comparison.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "run_id": output["run_id"],
                "groups": [{k: v for k, v in group.items() if k != "rows"} for group in groups],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
