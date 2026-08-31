---
title: 'Preserve signed manifest date strings on PowerShell 7.6'
type: 'bugfix'
created: '2026-08-31'
status: 'done'
baseline_commit: 'ac79721c38429807b8706f473214af5423ebcbfe'
context:
  - 'docs/superpowers/specs/spec-plan-5-cap1-publisher-authenticity.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** PowerShell 7.6 automatically converts the signed `MANIFEST.json.generated_at` JSON string into `System.DateTime`. Both Windows verification entrypoints then reject a valid signed candidate because the manifest contract requires the authenticated scalar to remain a string; converting it back would also lose the original offset, fractional precision, or `Z` spelling.

**Approach:** Parse only the authenticated top-level candidate manifest through a shared-in-behavior PowerShell helper that requests string-preserving date handling when supported and safely falls back on older supported runtimes. Keep the two independently shipped Windows verifiers synchronized and add executable regressions for current and minimum-compatible parsing behavior.

## Boundaries & Constraints

**Always:** Preserve the exact JSON text value of `generated_at` after parsing, including timezone spelling and fractional seconds. Maintain the declared PowerShell 7.3+ runtime contract. Apply the same behavior to the package-external publisher verifier and the package-internal candidate verifier. Continue to reject non-string, malformed, timezone-less, or otherwise invalid `generated_at` values before any image load, installation, or qualification action. Test both the `DateKind` path and the compatibility fallback.

**Ask First:** Any change to the supported PowerShell floor, signed manifest schema, logical-identity inputs, release key/signature policy, or deployment/qualification gate outcome requires separate approval.

**Never:** Modify or re-sign an existing immutable candidate; accept a `DateTime` and stringify it; weaken signature, SHA-256, duplicate-key, file allowlist, Docker identity, qualification, B-04, or B-08 checks; broaden date-preserving parsing to unrelated JSON inputs without evidence; activate or restart production as part of this code fix.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| PowerShell 7.6 path | Authenticated manifest contains `2026-08-30T00:00:00+00:00` | Both verifiers retain the value as the identical `System.String` | Any conversion or text drift fails the regression |
| Valid spellings | `Z`, fractional seconds, or non-UTC offset | Exact original text survives parsing and contract validation | No normalization or locale conversion |
| Older supported runtime | `ConvertFrom-Json` has no `DateKind` parameter | Helper uses the legacy parser and valid strings remain accepted | Unsupported parameter must never be invoked |
| Invalid type or timestamp | Number, boolean, object, malformed text, or missing timezone | Candidate is rejected before side effects | Fail closed with the existing verifier error boundary |

</frozen-after-approval>

## Code Map

- `tools/release_trust/verify-publisher.ps1` -- package-external trust entrypoint; parses the authenticated manifest before every normal or qualification mode.
- `deploy/verify-candidate.ps1` -- package-internal Windows verifier invoked directly and by normal publisher deployment.
- `tests/tools/test_release_artifacts.py` -- executable cross-entrypoint PowerShell release-verification regressions and helper harnesses.
- `tools/release_artifacts.py` -- authoritative manifest timestamp generator and validation reference; no production change expected.

## Tasks & Acceptance

**Execution:**
- [x] `tests/tools/test_release_artifacts.py` -- add red-phase, parameterized regressions that extract and execute each verifier's manifest parser and type/format validation under `pwsh`; cover exact preservation, invalid values, and a simulated no-`DateKind` fallback without performing Docker or deployment work.
- [x] `tools/release_trust/verify-publisher.ps1` -- add a capability-detected, string-preserving authenticated-manifest parser and use it only at the top-level signed-manifest parse point; validate the timestamp contract before qualification or candidate execution.
- [x] `deploy/verify-candidate.ps1` -- mirror the parser and timestamp contract at the package-internal signed-manifest parse point before image loading.
- [x] `tests/tools/test_release_artifacts.py` -- retain existing logical-identity tests and prove both entrypoints behave consistently without weakening existing fail-closed assertions.

**Acceptance Criteria:**
- Given a valid v2 or v3 signed-manifest model whose `generated_at` uses `+00:00`, `Z`, fractional seconds, or a non-UTC offset, when either Windows verifier parses and validates it under PowerShell 7.6, then the property is a `System.String` exactly equal to the source JSON value.
- Given a runtime surface without `ConvertFrom-Json -DateKind`, when the helper is exercised, then it selects the compatibility path without binding an unsupported parameter and preserves the behavior expected on PowerShell 7.3.
- Given `generated_at` is non-string, malformed, or lacks a timezone, when either manifest contract runs, then verification fails before Docker load, serial-tool installation, qualification execution, or production mutation.
- Given all targeted and existing release-artifact tests run, when the change is complete, then both PowerShell scripts parse successfully, all regressions pass, and signature/hash/allowlist/logical-identity and B-04/B-08 behavior remains unchanged.

## Spec Change Log

- 2026-08-31 / adversarial review: Replaced the initial locale-normalizing timestamp check with a bounded, ASCII-only ISO-8601 validator matching the existing Python `datetime.fromisoformat` plus timezone contract; added absolute-end anchoring, fixed module-qualified cmdlet binding, a real Windows PowerShell fallback run, and cross-runtime vectors for extended/basic calendar dates, ISO week dates, separators, compact/extended offsets, fractional units, normalized offset overflow, Unicode digits, and representable year boundaries. Avoids trailing-whitespace acceptance, command shadowing, compatibility false positives, and an unapproved signed-manifest schema narrowing. KEEP: exact signed text preservation, capability detection, both Windows entrypoints, and all pre-existing authenticity/B-04/B-08 gates.

## Design Notes

Capability detection must inspect the active `ConvertFrom-Json` command metadata rather than compare version numbers. This avoids guessing which patch release introduced `DateKind`. The fallback is safe because older supported runtimes do not perform the PowerShell 7.6 date conversion; the authenticated scalar and explicit timestamp validation remain the authority in both paths.

## Verification

**Commands:**
- `uv run pytest -q tests/tools/test_release_artifacts.py -k "powershell and manifest"` -- expected: new preservation, fallback, and rejection cases pass for both scripts.
- `uv run pytest -q tests/tools/test_release_artifacts.py` -- expected: complete release-artifact suite passes.
- `pwsh -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw tools/release_trust/verify-publisher.ps1)); [scriptblock]::Create((Get-Content -Raw deploy/verify-candidate.ps1))"` -- expected: both scripts parse without error.

## Suggested Review Order

**Authenticated manifest parsing**

- Bind the system JSON cmdlet and preserve date strings only when supported.
  [`verify-publisher.ps1:971`](../../../tools/release_trust/verify-publisher.ps1#L971)

- Mirror the package-internal parser before any candidate-side effects.
  [`verify-candidate.ps1:653`](../../../deploy/verify-candidate.ps1#L653)

**Timestamp contract**

- Match Python's existing ISO-8601 acceptance without normalizing signed text.
  [`verify-publisher.ps1:1064`](../../../tools/release_trust/verify-publisher.ps1#L1064)

- Enforce the same fail-closed contract inside the shipped candidate verifier.
  [`verify-candidate.ps1:746`](../../../deploy/verify-candidate.ps1#L746)

**Regression evidence**

- Execute extracted production helpers under current and legacy PowerShell engines.
  [`test_release_artifacts.py:784`](../../../tests/tools/test_release_artifacts.py#L784)

- Prove exact preservation and Python/PowerShell acceptance equivalence.
  [`test_release_artifacts.py:3718`](../../../tests/tools/test_release_artifacts.py#L3718)

- Cover real fallback, command shadowing, and invalid timestamp rejection.
  [`test_release_artifacts.py:3813`](../../../tests/tools/test_release_artifacts.py#L3813)
