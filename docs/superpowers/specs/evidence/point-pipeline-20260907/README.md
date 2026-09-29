# Point Acquisition and Database Verification - 2026-09-07

> Historical predeployment report. The subsequently approved signed release and extended 36-address test are complete; see [the deployment follow-up](../point-release-20260907/README.md) and [updated per-point table](../point-release-20260907/POINT-TABLE.md) for current status. Findings below remain the original baseline evidence.

## Outcome

- Target: `lenovo@100.109.90.21`, Windows `WIN-OAUCM8UQUGH`.
- Production remains running on `deploy-20260905.2`; no production deployment or restart occurred.
- Candidate table first delivered, then updated per row: [POINT-TABLE.md](POINT-TABLE.md).
- Physical read: 15 distinct FC3 registers, with one truncated response followed by a successful permitted retry.
- Target-side isolated database: all 15 valid physical raw registers persisted and read back from realtime and history tables.
- Synthetic positive-value tests: all 42 FC3 candidate rows passed addressing, candidate scaling, realtime UPSERT and history INSERT checks in both code variants. These deliberately use the supported unsigned word type, not the unsupported legacy s16 type.
- Three software bug classes were reproduced against the deployed image and fixed locally. The patched code passed the same database-backed experiment in a disposable process. It has NOT been deployed to production.
- Production acquisition acceptance is incomplete: model, point meanings, scaling and signedness remain unverified; 21 FC3 candidate registers and four conflicting FC1 candidates have no physical evidence.

## Physical Test

Authenticated runner: `C:\Ruisheng\tools\run_modbus_probe.ps1`, existing protected configuration and current image-bound receipt, no edits to signatures or approvals.

Run ID: `5c62e3d0-509d-4e74-9a1d-b86edf340dc1`.
Time: 2026-09-07 10:36:59 to 10:37:00 +08:00.
USB identity: FTDI `0403:6001/AI06JYFW`; `/dev/ruisheng-rs485`; 9600/8N1; unit 1; FC3 only.

| Request | Attempt | TX | RX | Result |
| --- | --- | --- | --- | --- |
| 0..5 | 0 | `010300000006c5c8` | `01030c0003000000000000000000009c34` | Valid CRC; 98.749 ms; `[3,0,0,0,0,0]` |
| 27..35 | 0 | `0103001b0009f5cb` | `fc` | Truncated, one byte; 66.361 ms; no value accepted |
| 27..35 | 1 | `0103001b0009f5cb` | `01031200030000000b000b000a0000000000000000437d` | Valid CRC; 116.31 ms; `[3,0,11,11,10,0,0,0,0]` |

Three read requests, one retry, zero device-register writes. The configured 500 ms minimum spacing was retained. This proves recoverable read communication for these two ranges, not long-term line stability or calibrated engineering values. The cause of the truncated response is not established; no baud-rate or wiring changes were made on speculation.

## Target Database Experiment

Time: 10:47:02 to 10:47:08 +08:00.
Retained test database: `test_point_pipeline_20260907_b8a4d285`.

The experiment created a new explicitly named database, owned by the existing gateway role, and created the existing ORM realtime/history table definitions with a Timescale history hypertable. It used the production PostgreSQL server but did not alter any production table or role. The connection secret was obtained on the target and passed only through process stdin; it was never logged or placed in command-line arguments, source artifacts, or reports.

Both disposable containers used pinned image `sha256:d846ae0f1243d2b16d0cbe7781a465ca3a7381aea4379ce4750c5564dcb6db95`, no USB mapping, no host mounts, no published ports, dropped capabilities, a read-only root filesystem, no-new-privileges, 256 MiB memory limit and 0.5 CPU limit. They shared the gateway network namespace only to reach the existing database. No live GW process or image files were edited.

Variant `deployed` ran the actual deployed modules. Variant `patched` verified the hashes of two local source modules and evaluated them only in its isolated Python interpreter. Both exercised `Registry -> FrameIngestor -> BatchWriter -> Repository -> PostgreSQL/TimescaleDB`, then selected persisted rows. The publisher was a recording stub; this is not a Redis delivery, API authorization, browser, or continuous polling test. Device/point registry definitions were diagnostic in-memory inputs, not production device configuration.

| Test | Deployed Code | Patched Code |
| --- | --- | --- |
| 15 physical raw register replays | Realtime 15, history 15; all exact | Realtime 15, history 15; all exact |
| 42 synthetic candidate address/scaling cases | Every point: realtime 1, history 2; exact | Same; final realtime timestamp equals latest history timestamp |
| Legacy s16 `0xffff` rejection, all 42 FC3 rows | FAIL: writes unsigned 65535 instead of rejecting unsupported type | PASS: realtime 0, history 0 |
| Four conflicting FC1 legacy configurations | FAIL: realtime 4, history 8 | PASS: rejected; both tables 0 |
| FC3 response with an FC4 point | FAIL: wrong point written | PASS: both tables 0 |
| Corrupted CRC | PASS: no rows | PASS: no rows |
| Queue read completes simultaneously with shutdown | FAIL: dequeued row lost; both tables 0 | PASS: realtime 1, history 1 |
| Drain 46 queued points on shutdown | PASS: realtime 46, history 46 | PASS: realtime 46, history 46 |
| History duplicate-key failure rolls back realtime UPSERT | PASS | PASS |

Total retained test database contents, including intentionally bad baseline rows: realtime 256, history 344. Device identifiers are prefixed `deployed-` or `patched-` to distinguish variants. Baseline accounts for realtime 151/history 197; patched accounts for realtime 105/history 147. These are diagnostic fixtures, not customer telemetry.

The report contains exact synthetic expected/observed rows, timestamps, per-address physical checks and source hashes. [validation-summary.json](validation-summary.json) records 15 successful evidence consistency checks. It is not an all-point production acceptance certificate.

## Local Fixes and Verification

- `ruisheng-gw/src/ruisheng_gw/persistence/batch_writer.py`: preserve the completed queue read before honoring stop/timer completion, then drain remaining queued records.
- `ruisheng-gw/src/ruisheng_gw/ingest.py`: reject unsupported types instead of silently decoding them as unsigned, reject ambiguous coil type/bit configurations, and reject point/response function-code mismatch.
- Regression tests added in `test_batch_writer.py` and `test_ingest.py`; no signed-type feature or legacy point import was enabled.

Validation:

- GW unit/property suite: 195 passed.
- Serial hardware and authenticated Modbus tool suite: 66 passed.
- Ruff check/format and mypy on the modified production modules passed. The retained diagnostic Python script also passes lint and type checking.
- An initial stdin script transfer timed out before any test database was created. A read-only check confirmed this; a file transfer then succeeded. No failed database fixture or running diagnostic container was left by that attempt.
- `remote-experiment-*.generated.ps1` retains the exact executed diagnostic payload. `verify_pipeline.py` subsequently received type annotations and formatting only; the executed payload remains available for provenance. The two tested production source hashes did not change.

## Preserved Production State

`target-pipeline-results.json` embeds before/after snapshots: all five production container IDs, images, start times and restart counts were identical; PostgreSQL and Redis were healthy. All protected hashes (site environment, active-release pointer, serial hardware configuration, probe configuration and entitlement) were unchanged.

Production counts before and after: users 0, devices 0, device_points 0, realtime 0, history 0. This is why a running frontend is not proof that production acquisition has been commissioned.

Final `remote_debug.ps1 Health` returned API database/Redis/service/status ready and Web 200. The separate gateway support request returned the expected source-ACL 403: reachable, not a claim of gateway readiness. The production services were left running.

No OS/Docker restarts, compose teardown, Git push/merge, production DML, account creation, subscription changes, or polling-gate bypass occurred. Disposable test containers were automatically removed; the isolated database, original serial audits and diagnostic source artifacts were retained. No production data was deleted.

## Remaining Work

1. Publish the reviewed fixes through the existing signed candidate process; do not patch the active signed release in place.
2. Extend the authenticated read-only diagnostic scope through its approved process to cover FC3 addresses 6..26. Do not bypass the existing fixed whitelist with raw serial commands.
3. Resolve actual model/firmware, signedness, point meanings and engineering scaling. Confirm against physical references before importing business names or claiming calibrated values.
4. Resolve the four FC1 address/bit conflicts, then perform their physical tests if applicable to the identified model.
5. Establish the eligible production device/point records and approved serial mapping, then verify continuous acquisition and production database increments per confirmed point. UI/account commissioning remains separate and was not bypassed here.
