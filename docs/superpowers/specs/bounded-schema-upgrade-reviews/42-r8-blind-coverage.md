# Frozen blind review coverage

- Input: `implementation.diff`, SHA256 `169da4cd1506b4e7e53bde50c7e32f997baa7ba296c5b47376230a323caff3fb`. The hash was verified before and after review. PowerShell `Get-Content` enumerated 21,068 logical lines; the supplied snapshot description states 21,069 lines. No content discrepancy was inferred because the SHA256 matches exactly.
- Instructions read completely: `C:/Users/admin/.agents/skills/bmad-review-adversarial-general/SKILL.md`. The explicit review assignment superseded the skill's quota of ten findings: at least ten concrete hypotheses were considered, and only evidence-supported findings are reported.
- Project access was limited to the frozen diff. No repository source, other project files, runtime state, prior conversation, or external documentation was consulted. Historical review/report bodies in the diff were not used as evidence and were not read for conclusions. No subagents were spawned.

## Production code read

Read every changed production-code hunk, including available context and removals: 37 files, 7,905 diff lines, 5,457 added lines, and 500 removed lines. This covers:

- Migration `0013_serial_polling_profile` and shared Device schema/index changes.
- API administrator bootstrap; device, point, and alarm changes; request schemas; transactional configuration versions; device repository writes and constraint translation.
- Gateway registry reconciliation, ingestion, server wiring, batch shutdown, framing, clocks, TCP polling, the complete new serial poller, connection input reset, serial bus lifecycle, and session handling.
- Web device API pagination/profile mapping, device create/edit/list views, mobile navigation, and error mapping.
- Desktop launcher/installer, Modbus probe and runner, the complete new remote administrator bootstrap script, full-upgrade controller, all changed target-updater code, hotfix coordination, maintenance, and maintenance preparation.

For modified files this means all code present in the diff, not the full reconstructed repository file. Unchanged function bodies omitted by the diff were unavailable. New production files were read completely. Any initially truncated tool output was reread in smaller blocks.

## Test support examined

Enumerated changed test definitions across all 26 test files (10,412 diff lines). Test bodies were sampled rather than read exhaustively. Samples included:

- The complete new serial-poller test file: fairness, quiet time, late/wrong slave and invalid frames, exception responses, empty startup, lifecycle/address reuse, cross-port session ownership, send timeout, slow ingestion, and ingestion cancellation.
- Serial API fixture setup; registry reconciliation/capacity and TCP transition cases; lifecycle integration stream fixtures and the over-capacity behavior.
- Bootstrap request/CLI test outline and database integration cases for concurrent creation, tenant visibility, rollback, status reconciliation, and real-image CLI coverage.
- Upgrade integration harness setup and substitutions; real backup/restore assertions; fixture database roles, permissions, functions, triggers, properties, sequences, and Timescale history; recovery fixture and outer dispatcher setup.
- Maintenance-marker validation tests, launcher call-order and installation tests, maintenance mutation/status tests, selected shared-lock/process-containment tests, snapshot deadlines and uncertain-intent tests, unknown-writer retry budgets, and apply failure/audit independence tests.
- Startup native-task grammar and fixed native-host identity tests, including actual ACL/file fixtures and the stated host/default-command checks.

The integration harness explicitly substitutes publisher verification, entitlement, network checks, some ACL checks, and application health in its general recovery fixture; its application containers run inert Python sleepers. Specific outer-recovery and ACL-focused tests restore additional real checks. These are useful focused proofs but do not establish a complete production deployment from this diff alone. No test result in historical reports was adopted as a result of this review.

## Concrete failure hypotheses considered

| Hypothesis / trigger | Assessment from visible code |
| --- | --- |
| Downgrade after serial address reuse destroys retained history or fails after irreversible schema changes. | Migration checks duplicates before changes and raises; no supported finding. |
| Fixed-profile points read outside registers 0–37 or use the wrong function code. | API and poll-read construction validate profile bounds and function code; no supported finding. |
| A failed commit broadcasts uncommitted configuration. | Broadcast occurs after transaction exit; tests exercise commit failure. |
| Concurrent serial address creation exposes database errors or duplicates live endpoints. | Partial unique index and named-constraint translation handle the visible path. |
| Registry refresh mixes device/point/alarm revisions or invalidates entries for runtime alarm latch changes. | Repeatable-read snapshot and excluded runtime comparison field address these cases. |
| Over-capacity serial configuration partially replaces the registry. | Build raises before reconcile, retaining the old registry; tests intentionally exercise this behavior. API capacity policy outside visible code was not inferred. |
| Old serial generations or responses from another port/device are ingested after a change. | Registry-entry identity, bus, device, function, and byte-count checks plus session ownership protect visible paths. Same-slave RTU wire ambiguity beyond the configured timeout/quiet interval remains a protocol assumption, not a newly established code finding. |
| A TCP poller waiting on its lock overwrites a newly bound serial transaction. | It rechecks registry identity and session generation/writer while holding the lock. |
| Clearing serial input dispatches a previously completed queued read into the new transaction. | Input generation, cancellation, and buffer clearing address the visible race. |
| Shutdown drops a row already dequeued when stop and queue-read complete together. | Batch writer retains a completed read before breaking. |
| Slow realtime/alarm publication monopolizes the serial receive callback. | Transaction callback has a timeout and the receive path only resolves the response future; cancellation relies on cooperative async callees. |
| Simultaneous first-admin creates or lost create receipts create multiple accounts or expose a password. | Table locks, transaction/audit checks, status verification, bounded stdin, and allowlisted receipts address the visible cases. |
| Missing shared audit mutex is prepared with an ACL that downstream readers reject. | Supported finding in `blind-findings.md`: new file inherits its parent ACL and preparation returns before protecting it. |
| Expired/lost leases permit further mutation or unsafe cleanup. | Ownership/expiry checks are used at writes, process loops, and cleanup boundaries; no additional supported finding. |
| Killing an upgrade parent leaves local Docker descendants running. | Job containment gates child dispatch and confirms process-tree termination. |
| A completed local client is mistaken for an unconfirmed daemon start or role/restart release. | Persistent mutation intents and specialized restore/snapshot reconciliation preserve uncertainty. |
| Snapshot helper outlives its owner indefinitely or is declared stopped before its dispatch is known to have happened. | Finite helper lifetime, shared deadline, observed-start requirement, and explicit stop reconciliation address visible paths. |
| Restore verification uses unrelated containers/volumes or accepts modified backup assets. | Operation/token/image/volume identity and asset hashes are checked. Full schema support is limited to the explicit fingerprint/expression/property SQL. |
| Recovery chooses the previous application after the target schema or application has been used. | Observed-head decision and application-start receipt forbid the visible unsafe rollback case. |
| Keyword-preserving schema changes pass the migration shape check. | Full deparser expressions, column metadata/collation, and index properties are compared. |
| Environment drift or a replacement backup silently changes site settings during recovery. | Complete source/derived-target byte hashes and checked replacement cover visible paths. |
| An expired maintenance marker or alternate maintenance entrypoint bypasses an active upgrade. | Marker is status-based without lease expiry, with checks in launcher/hotfix/maintenance/bootstrap paths. |
| Scheduled shell wrappers hide unguarded business startup. | Closed supported-host parsing handles many wrappers; arbitrary native executable behavior is explicitly outside that proof boundary. No additional independently established finding was reported. |
| Audit/journal failure prevents all independent fence and stop attempts. | Failure handling separately attempts role fencing, application stop, restore stop, and snapshot stop with independently checked authority. |

## Limits and execution

This was a static diff review. No Docker, SSH, database, hardware, UI, or repository test command was run. No runtime reproduction is claimed for the finding; it follows from the visible file-creation call, immediate return, and downstream exact ACL checks. The diff does not supply complete unchanged call graphs, deployment configuration, installed ACL state, or dependency implementations. Test bodies not specifically sampled, archived documentation, and full end-to-end behavior remain outside demonstrated coverage.

Only `blind-findings.md` and `blind-coverage.md` were created with `apply_patch`. Git state and all other files were left untouched.
