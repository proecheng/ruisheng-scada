## Finding

**[P1] `forfiles.exe /C` bypasses the required startup-host proof.**

- **Locations:** `tools/remote_full_upgrade/target-updater.ps1:1810` omits `forfiles` from unsupported execution hosts. It reaches the default `return $false` at `tools/remote_full_upgrade/target-updater.ps1:2010`, so the scheduled-task check at `tools/remote_full_upgrade/target-updater.ps1:1660` accepts it without examining the executable command.
- **Concrete trigger:** A scheduled action under a Docker-capable account with Docker on its PATH, using this input—**not executed during this audit**:
  ```text
  Execute: C:\Windows\System32\forfiles.exe
  Arguments: /P C:\Windows\System32 /M cmd.exe /C "cmd /d /c docker start ruisheng-api"
  ```
- **Consequence:** The task can explicitly restart a stopped business container without invoking the maintenance-aware launcher, including a retained old container during the post-migration/pre-replacement window. Disabling automatic restart does not prevent this explicit start. This bypasses startup exclusion; it does not establish a bypass of the separate database `NOLOGIN` fence.
- **Requirement:** `docs/superpowers/specs/spec-bounded-schema-upgrade-context.md:162` requires unsupported command-execution hosts to be refused or covered by reviewed fixed-identity proof. It also undermines the old-program exclusion and startup-guard requirements at `docs/superpowers/specs/spec-bounded-schema-upgrade.md:30` and `docs/superpowers/specs/spec-bounded-schema-upgrade.md:97`.
- **Correction:** Reject `forfiles` as an execution host unless a bounded, reviewed proof covers its command payload. Add inert top-level and nested classifier cases for both PowerShell versions; existing host tests at `tests/tools/test_remote_full_upgrade.py:2779` contain no `forfiles` case.

## Coverage And Evidence

- Read the complete specification and context; reviewed changed hunks across **37 production files**, surrounding guards, and supporting tests. The frozen SHA256 matches; **64 non-document postimages** match the project source.
- Examined all six authorized evidence directories, including available run metadata, JUnit results, asset inventories, failure timeline, collation results, and relevant report references. Recorded source hashes match the corresponding current files.

| Execution evidence | Verified recorded outcome |
|---|---|
| R8 tools | **179 passed**, no failures/errors/skips |
| Original integration | **5 passed, 1 failed** with a 900-second PowerShell timeout; Core Apply was not executed |
| Instrumented diagnostic | **1 passed**, kept separate from canonical evidence |
| Canonical supplement | **2 passed**, Windows PowerShell and Core Apply |
| R7/R8 collation fixtures | Different updater identities; R7 accepts recreated-check drift, R8 rejects it in both hosts |

The seven selected integration cases therefore have passing evidence from **original five + supplemental two**, not one wholly successful run. Inventory rows record **35/13/24** stopped containers respectively, with identity checks and retained storage; these are recorded observations, not a current runtime inspection.

## Limitations

- No code/tests were executed and no files, services, or Git state were modified.
- The original timeout and reported `docker_process_tree_incomplete` remain unexplained. Supplemental passes do **not** establish root cause. Raw fixture audit/journal files and diagnostic implementation/traces outside the authorized directories were not inspected.
- The original approved 43-file snapshot was unavailable for independent comparison.
- Apply fixtures substitute verification, entitlement, installed guards, ACL/network checks, and health behavior (`tests/integration/test_schema_upgrade_recovery.py:1158`), and invoke internal Apply (`tests/integration/test_schema_upgrade_recovery.py:1511`). They do **not** establish signed-candidate, real-application, target-deployment, or physical-device acceptance.
