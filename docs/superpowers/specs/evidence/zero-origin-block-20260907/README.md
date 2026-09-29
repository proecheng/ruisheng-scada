# B-11: bounded zero-origin whole-block compatibility experiment

Status: completed once at 13:30 +08; 6 FC3 TX, 1 retry, 5 valid responses.
Formal acceptance remains BLOCKED. See [RUN-RESULTS.md](RUN-RESULTS.md).
The remainder of this file preserves the plan declared before physical I/O.

## Authorization and purpose

The current conversation user accepted the prior recommendation with
"按照建议执行". Recorded at 2026-09-07T13:10:33+08:00 by this work session;
this is not a claimed client-message timestamp. Authorization is for one
bounded read-only whole-block compatibility experiment plus the necessary
signed diagnostic-tools build/install. B-10's previous authorization is consumed.

Question: can the device return all 36 raw registers when FC3 starts at zero,
and do shorter zero-origin reads agree with bracketing whole-block prefixes?
This does not validate nonzero addressing, physical point identity, calibration,
engineering units, or firmware correctness.

## Fixed physical scope

Target WIN-OAUCM8UQUGH / 100.109.90.21. Adapter 0403:6001/AI06JYFW,
/dev/ruisheng-rs485, 9600/8N1, unit 1, FC3 only.

| Step | Start (zero-based decimal) | Count | Requires previous valid |
| --- | --- | --- | --- |
| 1 | 0 | 36 | no |
| 2 | 0 | 27 | yes |
| 3 | 0 | 36 | yes |
| 4 | 0 | 6 | yes |
| 5 | 0 | 36 | yes |

Profile b11-zero-origin-block-fc3-r0-35-v1. One retry per step; maximum 10 TX
(80 transmitted bytes of read commands). 400 ms response timeout, minimum
500 ms after each response before the next request. A standard full response is
77 bytes; this profile alone has an 80-byte receive ceiling, leaving room to
detect trailing noise. Existing B-06/B-09/B-10 ceilings remain 64 bytes.
No scanning, FC1, write functions, physical changes, or automatic second run.
An exception or exhausted retry stops all later steps.

## Interpretation declared before physical I/O

- Recompute CRC, unit, FC, byte count and raw register arrays for each response.
- Compare step 2 against positions 0..26 in steps 1/3, and step 4 against
  positions 0..5 in steps 3/5.
- Brackets different: DYNAMIC_REFERENCE, not pass/fail of a physical point.
- Brackets equal but short read different: LENGTH_INCONSISTENT in this
  observation, not proof of a firmware root cause.
- All three equal zero: ZERO_UNINFORMATIVE; equality does not establish identity.
- All three equal nonzero: PREFIX_CONSISTENT_THIS_OBSERVATION, not calibration.
- Compare all three full arrays separately for repeatability; no forced
  tolerance or multiplier adjustment. Bracketing equality cannot exclude an
  intermediate transient. A success supports only bounded zero-origin
  compatibility and cannot clear B-10's nonzero-address counterevidence.

## Implementation and safety checks

Use a new clean worktree based on 4de3d31c5766f0f04905756965f83c564ecce4cc.
Modify only tools/probe_modbus_rtu.py, tools/run_modbus_probe.ps1 and focused tests.
Build deploy-20260907.3 with all five deployed image IDs reused. Authenticate via
the existing publisher trust and full archive/image verification, install tools
only without SiteEnvPath, and use a separate administrator-protected config.
Dry run must bind the exact plan with no device mapping before the one execution.

Acceptance checks:

1. Given B-11 config, dry run emits exactly five predeclared frames and no I/O.
2. Given any altered scope/count/order/dependency/budget, validation blocks I/O.
3. Given a timeout at any step, at most one retry occurs and later steps stop.
4. Given a complete 77-byte response, parsing preserves all 36 unsigned raw words;
   truncation, wrong byte counts, CRC errors, and extra noise are not valid.
5. Given old profiles, receive ceilings stay 64 bytes in Python and runner.
6. Given the real run, raw and independent runner audits bind config/script/image,
   budgets, run ID, sequence and terminal counts; target hashes equal local copies.
7. Given before/after target snapshots, production container identities, starts,
   restart counts, active release, original configs, entitlement, trust and DB
   counts are unchanged; no diagnostic container or maintenance lock remains.

Production stays deploy-20260907.1; continuous collection and formal acceptance
remain BLOCKED until device/firmware/point semantics and other gates are closed.
No production fixtures, restarts, pushes, PRs, merges, or trust changes.
