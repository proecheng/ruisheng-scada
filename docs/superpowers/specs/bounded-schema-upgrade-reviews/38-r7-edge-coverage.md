# Independent edge review coverage

Input: `implementation.diff`, 20,621 lines. SHA256 verified as `28dc646351acee3c9e2eca9abe2320370d2f819d75e383bf92c505e70ee385bd`.

Source root: `C:/ProgramData/Ruisheng/publisher-build/bounded-schema-multidevice-20260908`.

The edge-case-hunter skill was read completely and followed in sequence: identify the nonempty text diff; enumerate changed production paths and their boundaries; revisit identified edge classes and referenced guards; emit the findings array. Historical review documents were not read. Embedded report claims were not used as proof.

## Material actually inspected

The production-code diff hunks were read in paginated ranges for these paths:

- `alembic/versions/20260907_0013_serial_polling_profile.py`.
- API: `admin_bootstrap.py`, `api/alarms.py`, `api/devices.py`, `api/points.py`, `api/schemas/devices.py`, `api/schemas/points.py`, `core/config_changes.py`, and `db/repositories/devices.py` under `ruisheng-api/src/ruisheng_api/`.
- Gateway: `domain/registry.py`, `ingest.py`, `main.py`, `persistence/batch_writer.py`, `protocol/framer.py`, `scheduler/clock.py`, `scheduler/poller.py`, `scheduler/serial_poller.py`, `transport/connection.py`, `transport/serial_bus.py`, and `transport/session.py` under `ruisheng-gw/src/ruisheng_gw/`.
- Shared device model: `ruisheng-shared/src/ruisheng_shared/models/devices.py`.
- Web: `src/api/devices.ts`, `src/layouts/AppLayout.vue`, `src/utils/errors.ts`, and the three changed device views under `ruisheng-web/`.
- Tools: `install_ruisheng_desktop_launcher.ps1`, `probe_modbus_rtu.py`, `remote_admin_bootstrap.ps1`, `remote_full_upgrade.ps1`, `remote_full_upgrade/target-updater.ps1`, `remote_hotfix_deploy.ps1`, `remote_maintenance.ps1`, `remote_maintenance_prepare.ps1`, `run_modbus_probe.ps1`, and `start_ruisheng_local.ps1`.

One initial combined read was truncated around API device hunks. Those device hunks were reread separately before drawing conclusions. Subsequent reads used smaller ranges and explicit output budgets.

Referenced source inspected to validate the reported paths included API device creation/update and repository functions; registry construction, database loading and serial reconciliation; the gateway serial-task lifetime; both local and embedded-remote preparation functions and their caller; the updater's shared audit-file validation; and the registry's directly imported device/point classes.

## Edge classes revisited

- Empty/null and type boundaries in bootstrap requests, device/point updates, serialized receipts and profile selection.
- Address, register-span, request-count, response-size and serial-capacity transitions.
- Transaction commit versus notification failure; refresh construction versus publication; pending response identity during reconfiguration.
- Read/drain/ingestion cancellation, deadline boundaries, input generation changes, and serial cleanup.
- Missing versus existing filesystem assets, inherited versus explicit ACLs, file identity and maintenance-marker states.
- Maintenance acquisition/renewal/loss; child-process containment; snapshot-holder start/stop uncertainty; backup/restore asset identity.
- Upgrade prepared/fenced/backup/migration/application-start/completed phases; source-head versus target-head recovery; persistent Docker intent; failure cleanup authority.
- Startup command parsing branches for native tools, CMD, PowerShell, approved scripts and the fixed Windows host exception.
- Pagination completion and mobile layout state transitions.

## Findings validation

1. Missing audit mutex: the embedded remote preparation branch at `tools/remote_maintenance_prepare.ps1:242-249` uses `File.Open(...CreateNew...)`, closes it and returns before any file ACL setup. The same behavior exists in the local helper at lines 59-66. The compliant parent deliberately has inheritable SYSTEM/Administrators rules. The updater's `Assert-SharedAuditFile` at lines 197-218 requires the mutex DACL to be protected and to contain explicit rules for the allowed identities; `remote_maintenance.ps1` has the same requirement. The preparation caller at lines 440-452 returns `prepared` without validating the created mutex. This conclusion is static; no real ACL was modified.

2. Serial admission: the API creation and update paths check duplicate endpoints but do not reject an over-capacity port before committing. `Registry.build` at lines 83-99 counts all nondeleted serial rows, including disabled rows, and raises above 128 before reconciliation. `reload_serial_configuration` constructs the entire candidate first. A single pure in-memory Python probe imported the actual registry with bytecode writing disabled: 128 active COM3 rows loaded; adding disabled address 129 and disabling address 1 made candidate construction raise `serial capacity exceeded on COM3`; address 1 remained in the live registry. No database or serial hardware was used. The mutation admission check must also cover moves to a full port and serialize competing admissions.

## Limitations

This is a diff-scoped static path review with one focused in-memory registry probe, not an exhaustive whole-repository or production-runtime verification. Unchanged external helpers were expanded only where needed to validate changed behavior; all their transitive implementations were not inspected. Test-file additions, documentation and historical review artifacts were excluded from executable-behavior analysis. No broad test suites, Docker commands, database operations, SSH sessions, actual ACL changes, staging, source edits or deployment actions were performed. The final array contains two confirmed unhandled paths; it does not attest that all possible environment-dependent behavior is covered.
