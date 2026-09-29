# Point Pipeline Fix Release and Extended Read-Only Probe

Status: implementation approved by the user on 2026-09-07 in the current remote-testing thread.

## Approved Scope

Publish the tested gateway ingestion/shutdown fixes using the existing Ed25519 signed candidate workflow. Retain the current release trust principal, namespace, key and target site. Extend the authenticated physical probe to the remaining candidate FC3 register interval 6..26. Do not change firmware, serial parameters, device controls, subscriptions, accounts or production point definitions. No operating-system or Docker Desktop restart, compose down, volume deletion or schema change is authorized by this change.

The new closed profile is `b09-9600-8n1-unit1-fc3-r0-5-r6-26-r27-35`:

- Same FTDI `0403:6001/AI06JYFW`, alias `/dev/ruisheng-rs485`, 9600/8N1, unit 1, FC3.
- Exactly 0..5, then 6..26, then 27..35; each subsequent interval requires a valid preceding response.
- One retry per interval, at most six read requests, 400 ms receive timeout, at least 500 ms after the preceding response, and a 64-byte receive cap. The largest valid response is 47 bytes.
- Keep the legacy B-06 profile unchanged, with its original two intervals and four-request budget. The selected scope ID must match the exact ordered intervals and budget. No arbitrary ranges, coils or additional function codes are accepted.
- Preserve authenticated tool installation, immutable running image receipt, protected config snapshots, no-production-serial-access gate, exclusive USB identity validation, dual audit and pre/post production state checks.

## Fixes and Tests

1. `ruisheng-gw/src/ruisheng_gw/persistence/batch_writer.py`: retain a dequeued record when queue.get and stop/timer complete together. Test simultaneous shutdown and final drain.
2. `ruisheng-gw/src/ruisheng_gw/ingest.py`: reject unsupported types, malformed coil type/bit combinations and function-code mismatches. This is not implementation of legacy signed s16 support.
3. `tools/probe_modbus_rtu.py`: select only one of two fixed profiles and bind request construction to its scope. Default remains zero-I/O dry-run.
4. `tools/run_modbus_probe.ps1`: derive the permitted profile from the protected snapshot; independently validate every request, retry, ordered response and terminal count against that profile.
5. `tests/tools/test_modbus_probe.py`: test old/new profile separation, exact FC3 frames, invalid ranges/order/dependencies/budgets, stopped progression after failures, and Windows PowerShell/PowerShell Core audit checks.

Acceptance criteria:

- Given either closed profile, when dry-run is used, then the exact expected frames and budget are returned without opening the serial port.
- Given an unapproved interval, wrong scope/budget, write code or changed serial identity, when loading or constructing a request, then reject before device I/O.
- Given a failed preceding interval or Modbus exception, when execution reaches a dependent interval, then do not send it.
- Given completed queue.get and shutdown together, when the batch writer drains, then persist that record exactly once to realtime and history.
- Given unsupported s16 or an FC3 response assigned to an FC4 point, when ingesting, then submit and publish no telemetry for that configuration.
- Given a signed same-schema candidate, when approved Apply succeeds, then backups, health probes, network checks, immutable image identities and active pointer agree; otherwise use existing locked rollback/recovery.
- Given extended physical execution, when completed, then preserve exact raw audit copies and update candidate-table evidence without claiming calibrated semantics or enabling continuous production acquisition.

## Release Boundaries

Base source is the deployed `0a43b2a` plus previously tested audit-ACL compatibility commit `e4d7999`. Use a dedicated clean worktree so unrelated working-directory changes cannot enter the build. Perform local source commit only as build provenance; no GitHub push/PR/merge is required for this operation. The protected publisher installer remains unchanged. Install the new authenticated probe only from the verified candidate; create a separate protected extended configuration recording the current user's approval, without rewriting old evidence.

Target telemetry verification must use an explicitly isolated test database; production device/point tables are not commissioned by this change. Preserve all old releases, backup files, audit records and prior test databases. A valid FC3 response proves only that the interval is readable, not device model, physical point meaning, signedness or engineering conversion.
