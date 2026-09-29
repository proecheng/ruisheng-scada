# RS485 Remote Acceptance - 2026-09-07

## Outcome

- Target: `lenovo@100.109.90.21` (`WIN-OAUCM8UQUGH`).
- Active release: `deploy-20260905.2`.
- Physical read-only Modbus communication: PASS.
- Production serial polling: BLOCKED; not enabled and GW not restarted.
- No device controls, production database writes, account changes, subscription changes, OS/Docker restarts or repository pushes were performed.

## Correction to the Previous Handoff

The absence of a serial mapping in the production GW was intentional, not proof of failed USB attachment. The protected configuration at `C:\Ruisheng\site\serial-hardware.json` has `polling_approved=false` and unresolved device/protocol fields. usbipd has attached FTDI `0403:6001/AI06JYFW` to Docker Desktop WSL; the protected hardware task reports ready and `/dev/ruisheng-rs485` resolves to `/dev/ttyUSB0`. Opening the device in the authenticated probe independently verified the USB identity through `/sys/dev/char/188:0`.

## Reproduced Problem and Operational Repair

The installed probe release receipt still referenced `deploy-20260903.2` and GW image `sha256:d5366f66e0b07c2c829d2460cdb5b444afba415b5f3f639429f3bbac0ac9ce6b`. Dry-run `e47d06a7-25ea-4713-9103-7346b976eaaf` rejected the current GW image before any serial TX.

The existing protected publisher at `C:\ProgramData\Ruisheng\bin\verify-publisher.ps1` (SHA-256 `f37bb3ea11bc4fa039350e6309d4169f4a8072816153440cd00cbd95e5ba7df3`) was run against the active signed candidate with `-InstallSerialTools`. Signature, complete hashes, archives and all five loaded images passed. It reinstalled the authenticated tool set and regenerated the protected receipt. Publisher exit code 2 explicitly preserves the separate B-04 field gate; it is not a production-acceptance pass. The INSTALLED output and subsequent receipt/dry-run checks confirm the tool refresh succeeded.

- Refreshed receipt SHA-256: `65b48df17bd28dda19af018ec69e0ab1450bec4b05d3ec2e482d1d44c17a7bf5`.
- Bound GW image: `sha256:d846ae0f1243d2b16d0cbe7781a465ca3a7381aea4379ce4750c5564dcb6db95`.
- Successful dry-run: `c881cd59-6100-487c-b893-184056c710a2`.
- Existing restricted probe configuration was reused unchanged. Its embedded approval date is 2026-08-27; this turn's user instruction authorized resuming the remote test. No approval identity/signature or eligibility claim was fabricated.

## Physical Read Test

Run `cdbc89f4-2be4-467e-989b-c7df7329043a`, executed at 2026-09-07 10:00:32 +08:00.

Fixed scope: 9600 baud, 8N1, unit 1, FC3; read 0..5 and then 27..35 only after a valid first response; at most 4 requests with one retry per range and at least 500 ms between responses and subsequent requests.

| Range | TX | RX | Registers | Latency |
| --- | --- | --- | --- | --- |
| 0..5 | `010300000006c5c8` | `01030c0003000000000000000000009c34` | `[3,0,0,0,0,0]` | 237.773 ms |
| 27..35 | `0103001b0009f5cb` | `0103120003000000000000000000000000000000000272` | `[3,0,0,0,0,0,0,0,0]` | 161.804 ms |

Both responses passed address/function/length/CRC checks on the first attempt. Total TX: 2 read requests, 16 request bytes, zero retries, zero device register writes. Probe and runner exit codes were 0. USB identity was confirmed after exclusive open. Only these ranges are proven readable; model, point meanings, signedness, engineering units and conversion factors remain unverified.

## Preserved State and Verification

`before.json` and `after.json` show identical five-container IDs, image IDs, start times and restart counts. PostgreSQL/Redis remain healthy. API's internal healthcheck reports database/Redis/service ready; Web returns 200. The separate GW support probe returns the expected source-ACL 403, which proves reachability rather than readiness. No new claim of direct GW readiness is made from that 403.

The active pointer, site environment, serial hardware configuration, probe configuration and entitlement grant hashes are unchanged. Users/devices/device_points remain 0/0/0. Both maintenance locks are absent. No probe container remains. The runner independently recorded unchanged production state before/after the physical read.

The authenticated serial validator returns exit 2:
`[serial-hardware] BLOCKED: polling approval and device protocol parameters are unresolved`.

Local tests: `uv run pytest tests/tools/test_serial_hardware.py tests/tools/test_modbus_probe.py -q`: 66 passed.

## Evidence Files

Exact remote audit copies are retained beside this report:

- `modbus-probe-execute-20260907T1001.jsonl`: SHA-256 `0424997c2ac63d437851390b58a39a768c6d9217a524b0b2153ae90e62d11a0b`.
- `modbus-runner-cdbc89f4-2be4-467e-989b-c7df7329043a.jsonl`: SHA-256 `46b059236307a67a62749d6448bdea655949f65fd9fe07bd789516404a4fb408`.
- Failed preflight and successful dry-run audits are also retained.

Only the temporary probe container and publisher-managed temporary staging were cleaned by the existing tools. No release candidate, volume, retained backup or historical audit was removed.

## Remaining Boundary

This does not complete continuous production acquisition. Existing B-08 evidence still marks legacy BCMM/CBMM points unverified and not directly importable. Device/firmware identification and physical-reference correlation are needed before selecting production point names, signed decoding and scaling. Existing enablement requires a matching database device record and eligible point evidence; setting a serial environment variable alone would not satisfy it. No production mapping was created to bypass these requirements.
