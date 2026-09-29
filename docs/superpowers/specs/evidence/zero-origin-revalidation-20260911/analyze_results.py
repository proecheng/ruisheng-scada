"""Recompute bounded raw-frame evidence; never qualify physical points."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

EVIDENCE = Path(__file__).parent
PACKAGE = Path("C:/ProgramData/Ruisheng/publisher-output/deploy-20260911.1")


def compare(before: list[int], short: list[int], after: list[int]) -> dict:
    if not short or len(before) != len(after) or len(short) > len(before):
        raise ValueError("Invalid zero-origin comparison geometry")
    rows = []
    for index, value in enumerate(short):
        left, right = before[index], after[index]
        if left != right:
            status = "DYNAMIC_REFERENCE"
        elif value != left:
            status = "LENGTH_INCONSISTENT"
        elif value == 0:
            status = "ZERO_UNINFORMATIVE"
        else:
            status = "PREFIX_CONSISTENT_THIS_OBSERVATION"
        rows.append(
            {
                "response_position": index,
                "reference_before": left,
                "short_value": value,
                "reference_after": right,
                "status": status,
            }
        )
    counts = dict(Counter(row["status"] for row in rows))
    return {"rows": rows, "counts": counts, "physical_identity_confirmed": False}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    sys.path.insert(0, str(PACKAGE))
    import probe_modbus_rtu as probe

    config = probe.load_config(EVIDENCE / "approved-config.json")
    path = EVIDENCE / "physical.jsonl"
    if path.stat().st_size > 128 * 1024:
        raise ValueError("Unexpected audit size")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()]
    start, end = events[0], events[-1]
    receipt_path = EVIDENCE / "installed-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if start["event"] != "run_started" or end["event"] != "completed" or end["result"] != "valid":
        raise ValueError("Incomplete physical run; no compatibility conclusion")
    if start["plan"] != probe.normalized_plan(config):
        raise ValueError("Approved plan mismatch")
    bindings = {
        "script_sha256": digest(PACKAGE / "probe_modbus_rtu.py"),
        "config_sha256": digest(EVIDENCE / "approved-config.json"),
        "receipt_sha256": digest(receipt_path),
        "image_id": receipt["gw_image_id"],
        "approval_scope": probe.ZERO_ORIGIN_SCOPE_ID,
    }
    if any(start[key] != value for key, value in bindings.items()):
        raise ValueError("Authenticated run binding mismatch")
    if start["script_sha256"] != receipt["probe_sha256"]:
        raise ValueError("Installed script binding mismatch")
    if any(event["run_id"] != start["run_id"] for event in events):
        raise ValueError("Mixed run IDs")
    if events[1]["event"] != "port_verified":
        raise ValueError("Missing port identity")
    identity = events[1]["usb_identity"]
    if tuple(identity[key] for key in ("vendor_id", "product_id", "serial_number")) != (
        "0403",
        "6001",
        "AI06JYFW",
    ):
        raise ValueError("Unexpected adapter")
    middle = events[2:-1]
    if len(middle) % 2:
        raise ValueError("Unpaired audit event")
    frames, values = [], {}
    next_index, next_attempt = 0, 0
    for pair in range(len(middle) // 2):
        tx, rx = middle[2 * pair : 2 * pair + 2]
        if tx["event"] != "request_tx" or rx["event"] != "response_rx":
            raise ValueError("Unexpected event sequence")
        if next_index >= len(config.requests):
            raise ValueError("Unexpected additional request")
        expected = config.requests[next_index]
        for key, wanted in {
            "request_index": next_index,
            "attempt": next_attempt,
            "tx_number": pair + 1,
        }.items():
            if tx[key] != wanted or rx[key] != wanted:
                raise ValueError("TX/RX order or retry mismatch")
        if (
            tx["function_code"] != 3
            or tx["start_address"] != 0
            or tx["register_count"] != expected.register_count
            or tx["tx_hex"]
            != probe.request_frame(1, 3, expected, scope_id=config.approval.scope_id).hex()
        ):
            raise ValueError("Out-of-scope wire request")
        raw = bytes.fromhex(rx["rx_hex"])
        if len(raw) > config.budget.max_response_bytes or rx["rx_bytes"] != len(raw):
            raise ValueError("Receive budget/byte count mismatch")
        checked = probe.classify_response(raw, expected, 1, 3)
        for key in ("classification", "crc_valid"):
            if checked[key] != rx[key]:
                raise ValueError("Wire classification mismatch")
        if checked["classification"] == "valid":
            if checked["registers"] != rx["registers"]:
                raise ValueError("Raw register mismatch")
            values[next_index] = checked["registers"]
            frames.append(
                {
                    "step": next_index + 1,
                    "start": 0,
                    "count": expected.register_count,
                    "registers": checked["registers"],
                    "tx_hex": tx["tx_hex"],
                    "rx_hex": rx["rx_hex"],
                    "rx_bytes": len(raw),
                    "tx_at": tx["timestamp"],
                    "rx_at": rx["timestamp"],
                    "latency_ms": rx["latency_ms"],
                }
            )
            next_index += 1
            next_attempt = 0
        elif checked["classification"] == "modbus_exception" or next_attempt == 1:
            raise ValueError("Successful run cannot continue after terminal response failure")
        else:
            next_attempt = 1
    tx_count = len(middle) // 2
    if (
        next_index != 5
        or not (5 <= tx_count <= 10)
        or not end["tx_count_known"]
        or end["completed_tx_count"] != tx_count
        or end["attempted_write_bytes"] != 8 * tx_count
    ):
        raise ValueError("Incomplete bounded transmission accounting")
    groups = [
        compare(values[0], values[1], values[2]),
        compare(values[2], values[3], values[4]),
    ]
    repeatability = [
        {
            "response_position": index,
            "raw_values": [values[step][index] for step in (0, 2, 4)],
            "all_equal": values[0][index] == values[2][index] == values[4][index],
        }
        for index in range(36)
    ]
    output = {
        "run_id": start["run_id"],
        "started_at": start["timestamp"],
        "completed_at": end["timestamp"],
        "audit_sha256": digest(path),
        "completed_tx": tx_count,
        "retries": tx_count - 5,
        "device_write_functions_sent": 0,
        "frames": frames,
        "groups": groups,
        "full_block_repeatability": repeatability,
        "whole_block_frames_valid": True,
        "physical_meanings_confirmed": False,
        "formal_acceptance": "BLOCKED",
        "nonzero_address_issue_closed": False,
        "limitation": "Short observation only; bracket equality cannot exclude intermediate transients. No firmware, identity, engineering units or calibration proof.",
    }
    (EVIDENCE / "whole-block-comparison.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "run_id": start["run_id"],
                "completed_tx": tx_count,
                "retries": tx_count - 5,
                "groups": [group["counts"] for group in groups],
                "full_positions_stable": sum(row["all_equal"] for row in repeatability),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
