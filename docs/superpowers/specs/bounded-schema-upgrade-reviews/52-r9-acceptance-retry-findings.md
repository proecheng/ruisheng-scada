## Findings

**No confirmed actionable acceptance violations were found in the reviewed frozen implementation.** This is not approval of the unexecuted release, deployment, or hardware stages.

## Integrity and Coverage

- Verified diff SHA256: `01119e60b8da8b1a19d4b318cab5d59b98aa4d0b8565dd4e730c1b3f1079b9c0`.
- Matched all **20,150 after/context lines** against the source tree.
- Fully read the specification and its sole frontmatter context; reviewed changed hunks across **37 production paths**, referenced guards, and pertinent supporting tests. Considered the **17 concrete failure hypotheses** detailed in `REVIEW_COVERAGE`.
- Compared all **43 approved snapshot files**: 42 are byte-identical; CHANGELOG becomes byte-identical after removing exactly the permitted September 9 and September 10 additions.
- Verified that reversing only the `forfiles` rejection and nine fixed digest updates reconstructs the R8 updater hash, preserving its database, recovery, lease, and process-containment implementation.

## Evidence Verified

| Evidence | Verified result and boundary |
|---|---|
| R9 tools | Original **180 passed / 1 failed**; final **181 passed**, with recorded source hashes matching this tree. |
| R9 integration | **4 passed**: both PowerShell editions’ wrapper-task Recover refusal and complete bounded Apply fixtures. Asset records identify **50 unique nonrunning containers**—44 exited, six created—with ownership/image/no-host-port checks and retained storage. |
| R8 integration | Original **5 passed / 1 failed**, injected diagnostic **1 passed**, and canonical uninstrumented rerun **2 passed**, retained separately. |
| Focused evidence | Reviewed collation counterexamples, forfiles red/green/main-guard coverage, writer-clock diagnostics, and all nine permitted target observations. |
| Windows identity repinning | Same eleven recorded task identities/actions; only nine observed file digests changed. The implementation retains exact host/DLL/export/argument, protected-ancestry, and sidecar checks—not general permission based on a valid signature. |

The clock diagnostics establish a possible false-failure mechanism, **not the unique cause of the original failure**. Likewise, later successful Apply runs do not establish the unique cause of the earlier process-tree error.

## Acceptance Limits

- **Local fixtures:** Application containers sleep instead of running API/GW/Web (`tests/integration/test_schema_upgrade_recovery.py:1072`). The general fixture substitutes publisher verification, entitlement, installation/network/ACL checks, and health handling; targeted guard tests restore selected production checks (`tests/integration/test_schema_upgrade_recovery.py:1158`).
- **Signed candidate and real applications:** These records do not establish acceptance of the final signed release or its real application startup and health.
- **Target:** Observations concern the old `deploy-20260907.1` release on **0012**, not deployment of this candidate. The installed guard receipt is absent, and the Docker-start helper ACL remains correctly rejected pending the scoped installation correction.
- **Hardware:** PnP and USBIPD observations have different visibility; neither proves functional Modbus or B11 acceptance. No physical transmission was demonstrated.
- **Audit execution:** Read-only inspection only; no tests, applications, services, tasks, or hardware were executed. Referenced raw artifacts outside the expressly allowed locations were not accessed.

These release and target gates remain explicitly unfinished in `docs/superpowers/specs/spec-bounded-schema-upgrade.md:79`; fixture success does not close them.
