# B-10 Bounded Read-Only Address Correlation

Status: user approved build, signed diagnostic installation and one physical run in the current conversation on 2026-09-07. Approval was recorded at 2026-09-07T12:31:17+08:00, after the user's reply "批准" to the exact six-step request. This is not a point-calibration or production-onboarding approval.

## Scope

Only extend `tools/probe_modbus_rtu.py`, `tools/run_modbus_probe.ps1` and their regression tests with `b10-address-correlation-fc3-r0-35-v1`. Preserve B-06 and B-09 unchanged.

Fixed FTDI 0403:6001/AI06JYFW, /dev/ruisheng-rs485, 9600/8N1, unit 1, FC3. Exact ordered `(start,count)` sequence: `(0,27),(6,21),(0,27),(18,18),(27,9),(18,18)`. Every step after the first requires a valid preceding response. At most one retry per step, twelve read requests total, 400 ms receive timeout, at least 500 ms after the preceding receive, 64-byte receive cap. Largest valid response: 59 bytes. No other address, function, serial parameter, physical state change or repeat experiment is authorized.

Use a clean worktree based on deployed source 2150b5ee904760ce0af4483c201009b8744669a2. Make a local source commit only as signed-build provenance; do not push, create a PR or merge. Build a new immutable candidate through the existing builder's prebuilt-image API, reusing all five exact deployed image IDs. Manifest image source references must disclose that reuse. Do not rebuild application images or replace a signed package's files.

Install only authenticated serial tools through the package-external protected publisher and its `InstallSerialTools` mode. Preserve current running containers, active release, database head, site environment, subscription, trust root, previous packages, receipts/backups and historical audits. Create a separate protected config for the approved scope. Perform zero-I/O dry-run followed by this one bounded physical run through the protected runner. No operating-system, Docker or production-container restart; no production point import or collection enablement; no device register writes, FC1 or unknown/private codes.

## Acceptance

- Given the exact new profile, when loading and dry-running, then return only the six fixed frames and twelve-TX ceiling without opening serial I/O.
- Given modified ranges, scope, order, prerequisite, retries, budget or a write function, when validating, then reject before I/O.
- Given failure at any of the six steps, when retry policy ends, then stop without transmitting subsequent requests.
- Given repeated reference frames, when the runner validates audit events, then bind each to its distinct request index and terminal counts; a replayed earlier index must fail.
- Given authenticated installation, when the protected receipt is read, then its script hashes match the new candidate and its GW image is the unchanged running immutable image ID.
- Given successful physical execution, when evidence is collected, then verify frame address/function/length/CRC, dual audit completeness, exact order and budget, and unchanged pre/post production state.

## Interpretation and Stop Conditions

Compare step 2 with positions 6..26 of steps 1 and 3. Compare step 5 with positions 9..17 of steps 4 and 6. Changed bracketing values are dynamic/inconclusive. Stable bracketing values that disagree with the subread indicate observed address-correlation inconsistency. Matching the unshifted prefix supports a zero-origin-response hypothesis, not a proven firmware root cause. Identical target slices and prefixes provide no discrimination. Equal bracketing samples do not exclude a transient intermediate state.

Even matching overlapping reads do not establish model, firmware, point meanings, signedness, engineering scale, units or physical accuracy. Existing B-08 calibration/qualification and production-onboarding gates remain independent and blocked until their own evidence and approvals close. Do not silently alter multipliers or treat prior test-database replay as continuous production acquisition.
