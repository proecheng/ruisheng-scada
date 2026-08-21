---
title: 'Remote application maintenance'
type: 'feature'
created: '2026-08-21'
status: 'done'
baseline_commit: 'd61c54aea7cad7723a982ee3efe81b69f7788ad9'
context:
  - 'docs/superpowers/specs/spec-remote-maintenance-upgrades-subscriptions/SPEC.md'
  - 'docs/REMOTE_DEBUG.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Application lifecycle on the Windows target still relies on ad hoc Docker commands that can overlap a patch, expose configuration, leave a partial stop, or remove data.

**Approach:** Add an allowlisted SSH tool for read-only status and data-preserving stop/start/restart, with security gating, recoverable locking, drift checks, health verification, and correlated audit.

## Boundaries & Constraints

**Always:** Use SSH `BatchMode` and strict known-host checking. Record remote-derived Windows user, computer, and `SSH_CONNECTION`; report effective authentication posture and block mutations unless password authentication is disabled. Require an 8–200 character reason without control characters. Preserve all volumes and loopback bindings. Stop in `web -> api -> gw -> redis -> postgres` order; start dependencies, migration, then apps through production Compose. Hash Compose/env inputs, reject phase-to-phase drift, and verify all five persistent services internally.

Serialize maintenance and patching with an atomic leased lock containing operation ID, action, PID/start, target, and expiry. Reclaim only an expired lock without a matching process and audit it. During migration, acquire shared and legacy hotfix locks in fixed order. Emit allowlisted fields only. Write hash-linked target JSONL and a correlated operator mirror; this detects accidental editing, not a hostile administrator.

**Ask First:** Changing OpenSSH, Windows accounts/ACLs, Tailscale, production configuration/topology, or executing a real lifecycle action requires fresh approval.

**Never:** Delete/recreate volumes; use Compose `down`, mutable tags, password fallback, public management ports, caller shell text, unrestricted logs, or device control. Billing cannot invoke this tool. Status/dry-run cannot mutate target state. Never output Compose/env contents, credentials, tokens, or request URLs.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Status/security | Any stack state | Allowlisted site, host, SSH posture, lock, service, image, health | Failure is non-zero and read-only |
| Stop app | Running stack; posture safe; locks available | Ordered stop preserving containers and volumes | Stop at first failure; return stopped/remaining services and `StartApp` recovery hint |
| Start/restart | Stable configuration hashes | Start current release and verify five services | Audit partial failure; keep healthy dependencies running |
| Conflict/stale lock | Shared/legacy lock exists | Reject active/uncertain; reclaim proven stale | Report owner/action/age only |
| Duplicate | Terminal operation ID | Return result without Docker action | Reject conflicting reuse |
| Dry-run | Any lifecycle action | Return allowlisted plan and before/after snapshot identity | Containers, hashes, locks, state, and audit offsets must remain equal |

</frozen-after-approval>

## Code Map

- `tools/remote_maintenance.ps1` -- action surface and remote lifecycle state machine.
- `tools/remote_maintenance_prepare.ps1` -- separately approved restricted ACL preparation.
- `tools/remote_hotfix_deploy.ps1` -- shared/legacy lock transition.
- `tools/remote_debug.ps1` -- compatible SSH conventions.
- `tests/tools/test_remote_operations.py` -- maintenance contracts.
- `docs/REMOTE_DEBUG.md` -- workflows and recovery guidance.

## Tasks & Acceptance

**Execution:**
- [x] `tools/remote_maintenance.ps1` -- implement preflight, snapshots, lifecycle, validation, leased lock, drift/health checks, safe output, and audit.
- [x] `tools/remote_maintenance_prepare.ps1` -- provision restricted local/target audit and operation-state locations, plus a non-recursive site-root boundary for the legacy compatibility lock, only after key-only SSH posture is effective.
- [x] `tools/remote_hotfix_deploy.ps1` -- reserve shared then legacy locks in fixed order, renew through local preparation, and hand both to the remote deployment process without an unlocked transition.
- [x] `tests/tools/test_remote_operations.py` -- cover the matrix, injection, PID reuse, lock transition, leakage, and forbidden commands.
- [x] `docs/REMOTE_DEBUG.md` -- document concise status/dry-run/lifecycle commands and recovery guidance.

**Acceptance Criteria:**
- Given the target currently reports `passwordauthentication yes`, when status and lifecycle dry-runs execute, then posture is visible, snapshots are unchanged, no secrets are emitted, and every real mutation remains blocked until separately approved SSH hardening is complete.
- Given simulated lifecycle, active/stale locks, PID reuse, drift, duplicate identity, partial stop, and failed-health states, when actions execute, then tests prove fail-closed behavior, deterministic recovery information, audit correlation, and volume preservation.
- Given either legacy hotfix or shared maintenance lock is active, when maintenance or updated hotfix starts, then it is rejected before Docker, state, or audit mutation.
- Given final code, when tool tests, Ruff, Windows PowerShell 5.1/PowerShell 7 parsing, remote status/dry-run snapshots, and internal health checks run, then all pass while real lifecycle actions remain unexecuted.

## Spec Change Log

- 2026-08-21 / adversarial review 1: Added effective SSH gating, remote identity, limited audit guarantees, leased PID-safe locks, dual-lock transition, drift checks, safe output, dry-run snapshots, partial outcomes, and PowerShell 5.1/7 checks. Avoids password-enabled mutation, lock races, leakage, and false tamper-resistance. KEEP: loopback, volumes, billing/device separation, no live mutation.
- 2026-08-21 / scope split: Deferred Windows restart/shutdown into an independent delivery. This spec now covers only application status and lifecycle maintenance, reducing privilege and state-machine coupling.
- 2026-08-21 / adversarial review 2: Resolved the conflict between the required legacy top-level lock and the earlier state/audit-only ACL task. The shared lock now lives in the restricted state directory; preparation restricts only the site-root directory entry needed to protect the legacy lock and does not recursively rewrite existing site files. Avoids both an ordinary-user lock bypass and broad production ACL churn. KEEP: fixed shared-then-legacy order, separately approved ACL preparation, no lifecycle execution during preparation.

## Design Notes

Restricted state/audit live under `C:\Ruisheng`; the mirror uses `%LOCALAPPDATA%\Ruisheng`. Current `passwordauthentication yes` blocks mutations. Host power and scheduled tasks are absent.

## Verification

**Commands:**
- `uv run pytest -q tests/tools/test_remote_operations.py` -- `40 passed`, including reservation-exit and deployment-lock-loss injection.
- `uv run pytest -q tests/tools` -- `135 passed, 1 skipped` (Docker release-artifact E2E remains opt-in).
- `uv run ruff check tests/tools/test_remote_operations.py` -- passed with no findings.
- Windows PowerShell 5.1 and PowerShell 7 parsers over all three scripts -- passed with zero syntax errors.
- Project regression -- expanded Python run reached `825 passed, 61 skipped` with one pre-existing API integration-fixture failure deferred; frontend `83 passed`; production build succeeded with the existing ECharts chunk-size warning.
- `.\tools\remote_maintenance.ps1 -Action Status` -- expected: read-only allowlisted target summary including password-auth posture.
- `.\tools\remote_maintenance.ps1 -Action StopApp -Reason test-only -DryRun` with before/after snapshots -- expected: equal target state and a security block in the plan.
- `.\tools\remote_debug.ps1 Health` -- expected: API, GW, and Web return 200 internally.

## Suggested Review Order

**Lifecycle Safety**

- Start with the allowlisted operator entry point and approval boundary.
  [`remote_maintenance.ps1:2`](../../../tools/remote_maintenance.ps1#L2)

- Follow the read-only status/dry-run split before any lifecycle mutation.
  [`remote_maintenance.ps1:1310`](../../../tools/remote_maintenance.ps1#L1310)

- Verify effective SSH posture blocks every password-capable mutation path.
  [`remote_maintenance.ps1:493`](../../../tools/remote_maintenance.ps1#L493)

- Inspect leased dual-lock acquisition, PID reuse defense, and stale reclamation.
  [`remote_maintenance.ps1:872`](../../../tools/remote_maintenance.ps1#L872)

- Review restricted ACL preparation as an independently approved operation.
  [`remote_maintenance_prepare.ps1:116`](../../../tools/remote_maintenance_prepare.ps1#L116)

**Hotfix Coordination**

- See how preflight, tests, builds, and uploads reserve both locks.
  [`remote_hotfix_deploy.ps1:151`](../../../tools/remote_hotfix_deploy.ps1#L151)

- Trace the no-gap ownership transfer from reservation to deployment.
  [`remote_hotfix_deploy.ps1:824`](../../../tools/remote_hotfix_deploy.ps1#L824)

- Check lease-guarded Docker execution during long remote operations.
  [`remote_hotfix_deploy.ps1:657`](../../../tools/remote_hotfix_deploy.ps1#L657)

- Confirm lock loss blocks unlocked environment rollback.
  [`remote_hotfix_deploy.ps1:984`](../../../tools/remote_hotfix_deploy.ps1#L984)

**Evidence And Operations**

- Execute the reservation-exit injection covering the former unlocked window.
  [`test_remote_operations.py:620`](../../../tests/tools/test_remote_operations.py#L620)

- Execute the health-path lock-loss propagation regression.
  [`test_remote_operations.py:675`](../../../tests/tools/test_remote_operations.py#L675)

- Finish with operator recovery guidance and production boundaries.
  [`REMOTE_DEBUG.md:85`](../../REMOTE_DEBUG.md#L85)
