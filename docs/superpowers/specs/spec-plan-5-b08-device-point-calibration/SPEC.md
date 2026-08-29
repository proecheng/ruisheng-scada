---
id: SPEC-plan-5-b08-device-point-calibration
companions:
  - '../spec-plan-5-b08-device-identity-point-calibration.md'
  - '../spec-plan-5-b08-device-identity-point-evidence.md'
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete contract for B-08 device evidence. Plan 5 B-07 remains the separately defined backup/restore blocker. The machine evidence named by the companions is not an automatically applicable production profile.

# Plan 5 B-08 Device Identity and Point Calibration Evidence

## Why

B-06 established only that two FC3 register ranges are readable on the self-developed device. Historical BCMM/CBMM data, signed encoding, point semantics and scaling remain unresolved, so production onboarding would risk silently decoding or naming data incorrectly.

## Capabilities

- id: CAP-1
  intent: The project can preserve every recovered legacy point candidate with its source identity, byte location, confidence and conflicts.
  success: The evidence parses to exactly 46 non-deployable candidates, split BCMM=6 and CBMM=40, and binds them to the unchanged source MDF hash.
- id: CAP-2
  intent: Reviewers can distinguish model alignment from confirmed device identity.
  success: CBMM is recorded only as medium-confidence, BCMM remains a candidate, and neither is marked confirmed from readable registers alone.
- id: CAP-3
  intent: Reviewers can classify each point's identity, semantics, encoding, calibration and runtime support without hiding uncertainty.
  success: The repository/offline v5 Schema and validator are complete: a fail-closed validator rejects caller eligibility booleans and returns ELIGIBLE only when a canonical gate digest binds artifact Schema v1, the executable validator source digest, calibration content v3, reference content v4, raw-observation content v4 and the trust policy; four-role signatures, field states, per-point role/subject, typed line readback, classification-specific evidence content, trusted runtime attestation, a trust-policy-recognized verifier-signed `ReleaseVerificationReceipt` subsequently bound by the independent `EligibilityApproval`, and contradictions all close. Current evidence maps deterministically to BLOCKED while recovered `s16` text remains a hypothesis. Offline completion does not satisfy external trust-root freshness or Windows verifier runtime/signing-key isolation.
- id: CAP-4
  intent: A future approved physical calibration can resolve each point with evidence appropriate to its measurement kind.
  success: Analog evidence contains exact-N calibration/reference/raw samples for A/B fitting, C holdout, A' return, thresholds and uncertainty, or a device/firmware-bound authoritative definition plus A/B/A' agreement; binary evidence covers exact-N transition, return, chatter/negative controls and address semantics for coils, register bits or proven whole-register discrete values; counter evidence covers exact-N increments, monotonicity, modulus, rollover/reset/saturation and persistence. Every sample closes across three independent artifacts and these kinds cannot inherit one another's method.
- id: CAP-5
  intent: A new implementation story can receive an explicit prerequisite set without B-08 changing production.
  success: Reserved B-09 receives the complete signed-type surface, atomic disabled onboarding, concurrency/tenant safeguards, a shared serial lock and dry-run requirements; its runtime target must come from a manifest v3 candidate with the complete authenticated qualification toolchain and a trust-policy-recognized verifier-signed `ReleaseVerificationReceipt` that attests the OpenSSH-signed candidate, actual loaded images and independently observed API-image migration head. The receipt binding must then be approved by the independent post-run `EligibilityApproval`, without inheriting permission for a GW rebuild, canary or new transmission. Manifest v2 cannot qualify B-09 or canary.

## Constraints

- The original MDF is read-only; its physical page must not be named as a specific SQL table without catalog evidence.
- Existing B-06 observations prove only range readability and cannot prove model, point name, sign, unit or scaling.
- No new Modbus transmission, physical state change, production database write, Compose change, gateway rebuild/enablement, control, alarm or notification is authorized.
- Unsupported encodings must remain unsupported; scaling must not be used to disguise a decode defect.
- Calibration is not authorized by B-08. Before observing results, a separately reviewed immutable `CalibrationRunApproval` must bind approver roles, immutable profile input, validator source digest, device/firmware/point/line identity, state plans, collector tools, reference instrument, exact read-only TX/safety scope, time window and expiry. It must not bind future results. After the run, a separate `EligibilityApproval` binds the final canonical gate, evidence/runtime/receipt set and contradictions; both approval layers verify role identities and signatures under an external trust policy rather than caller-supplied strings.
- Installing or using reference instruments, changing physical state, power cycling/restarting the target, writing the site Profile/database, generating or applying an override, rebuilding/enabling GW, canary polling, alarms and notifications all require their own later authorization.
- Calibration thresholds require a defensible uncertainty budget, an exact predeclared N of at least three new synchronized samples per state, meaningful A/B/C separation and a fresh A' return; caller-supplied eligibility booleans are never authoritative. For every `(point, run)`, the calibration, reference and raw-observation roles must each have exactly one artifact; equivalently each `(point, role, run)` is globally exactly-one, and each sample/event/time/value identity must agree across all three roles.
- Line address and 8N1 values remain unresolved profile inputs even though B-06 used address 1 at 9600/8N1; each line field requires evidence bound to the same device identity and a recomputable POSIX termios/udev or Windows DCB/SetupAPI typed readback with stable-path/USB provenance, and no validator or legacy mapper may promote B-06 request parameters to deployment defaults.
- FC2 discrete-input identity is not FC1 coil identity: every FC2 point requires dedicated `FC2_ADDRESS_TRANSLATION` evidence owned by `DISCRETE_INPUT_ADDRESS_TRANSLATION`; FC1 evidence cannot be renamed or reused to close it.
- Calibration kind is a closed `unknown/analog/binary/counter` union: affine mapping is valid only for analog, binary uses state transitions, and counter uses monotonicity/rollover evidence.
- Evidence integrity and authenticity are separate: every point reference must match an allowed owner/role/subject, all trust keys must be active, valid and unrevoked at artifact observation time, runtime PASS must come from a trusted attested runner, and release target fields must come from a trust-policy-recognized verifier-signed `ReleaseVerificationReceipt` whose ID derives from its protected snapshot and binds the OpenSSH candidate, actual loaded images and independently observed migration head. Its binding must be included in the later independent `EligibilityApproval`; hash matching alone is insufficient.
- Artifact processing is fail-closed and bounded before allocation or extraction: release JSON/config is at most 4 MiB, an archive has at most 32,768 members, one member is at most 8 GiB and total scan/expansion is at most 32 GiB; profile-bound artifacts have a 256 MiB aggregate budget and each actual read is at most the smaller of 64 MiB and its declared size; extractor source/supporting evidence is at most 1 MiB. Remote-maintenance audit input is separately limited to 16 MiB, 64 KiB per line and 50,000 entries before any lifecycle change, and a cached verified snapshot is reusable only when protected identity/metadata and content SHA-256 are unchanged.
- Release qualification chronology is causal: the signed receipt `verified_at` is no later than the bound raw runtime start and signed runtime observation, and every final `EligibilityApproval.approved_at` is no earlier than the receipt, evidence and runtime it approves. Violations remain BLOCKED/INVALID even when all signatures and hashes are otherwise valid.
- Manifest v2 remains compatible only with general candidate verification and existing remote maintenance. B-08 qualification/receipt, Plan 5 G0-06/G4-01, B-09 and canary require manifest v3, its complete authenticated qualification toolchain and a valid `ReleaseVerificationReceipt`.
- Qualification starts only from a protected package-external bootstrap; the v3 candidate contains the signed static toolchain but no executable launcher. Publisher modes are the closed set `ValidatorSchema`, `ValidatorProfile`, `ValidatorLegacy` and `Receipt`, with exact mode-specific parameters and no v2 fallback.
- The toolchain archive is canonical single-member gzip plus deterministic strict USTAR with exact regular-file allowlist, zero padding and bounded zero trailer. General OCI processing is bounded before allocation by 4 MiB JSON/config, 32,768 outer members, 8 GiB per member, 32 GiB total expansion and 64 MiB aggregate Docker metadata; raw Docker outer and nested-layer tar headers reject every PAX/GNU extension before its payload can be allocated, and duplicate outer members are invalid. Migration inputs are at most 4,096 files, 2 MiB each and 64 MiB total, and OCI whiteouts must be zero-length regular files with valid non-dot targets.
- The receipt derives the unique Alembic head from the verified, actually loaded API image's final overlay without importing or executing migration code. It rejects duplicate outer Docker members and validates the source-only configuration, literal revision graph, missing/duplicate revisions and cycles before binding the observed head to manifest and receipt.
- Build and receipt share a host-global `.<candidate_id>.candidate-tags.lock` under the protected system lock root; same-candidate overlap fails, different candidates may proceed, and abnormal exits do not poison future acquisition. If lock release fails after atomic receipt publication, a distinct published-error retains the complete receipt and loaded candidate tags. Windows runtime budgets are 32,768 actual files including the runtime manifest, 32,768 directories, 512 MiB per file, 32 GiB total and 4,096 UTF-8 path bytes. Windows system qualification requested through the generic Python bootstrap fails closed and directs the operator to the protected PowerShell publisher. Every POSIX and Windows qualification exit path must terminate and boundedly reap the complete process group or gated kill-on-close Job tree, including descendants that outlive the root.
- Trust-root provisioning is an external blocker even after offline validator completion. An authority outside the rollbackable repository/system disk must maintain at least the high-water tuple `(root_id, root_version, revocation_sequence, root_sha256)`, reject rollback or same-version hash substitution, and use TPM NV/equivalent monotonic hardware state or an independent remote freshness witness because administrator and whole-disk rollback are in scope.
- Windows verifier provisioning is a separate external blocker: the self-contained Python 3.11 runtime, locked dependency closure and bootstrap need protected owner/ACL/final-path/file-identity evidence, while the Profile-approved receipt signing key and agent/channel must be isolated from caller-controlled runtime, Docker, SSH configuration and user agents. A TPM/CNG/HSM non-exportable key or an equivalently isolated dedicated system agent is required before a receipt can be accepted for B-08/B-09/canary.
- A Python process cannot honestly prove that already loaded/cached executable bytes are identical to a later read of its mutable source path. Source-digest checks therefore detect disk changes but do not close loader-byte identity; B-08-W must disable or isolate replaceable bytecode caches and start the validator from the protected, immutable bootstrap/runtime boundary that owns the measured source and dependency closure.
- ELIGIBLE is a qualification result only. It never authorizes transmission, rebuilding, canary polling, continuous collection or production cutover, all of which retain their independent specification, approval and Plan 5 gates.

## Non-goals

- Confirming the physical device as BCMM or CBMM.
- Importing a point map or enabling continuous production polling.
- Implementing B-08 runtime fixes, control functions, alarms, notifications, automatic upgrades or subscription charging.

## Success signal

The repository contains a reproducible candidate ledger and a complete offline v5 qualification implementation while the production database, gateway permissions and device traffic remain unchanged. B-08 remains blocked until trust-root anti-rollback freshness, Windows verifier runtime/signing-key isolation, a valid v3 `ReleaseVerificationReceipt`, physical identity/calibration, B-09 implementation support and every contradiction close.

## Assumptions

- The high-confidence fixed-column mapping inferred from the 21-column physical rows and old C# field mapping is sufficient for candidate preservation, but not for asserting the SQL table identity.
- Existing B-06 audit artifacts remain the authoritative source for the two previously transmitted read ranges.

## Open Questions

- What model, firmware version and authoritative point-map version identify the current physical device?
- Which points can be placed in independently measured A/B/C/A' states, with what safety controls and tolerances?
- Which points are analog, binary or counter, and which approved state-transition or monotonicity/rollover plan applies to each non-analog point?
- Is every recovered `s16` declaration authoritative for the current firmware, including the four FC1 bit candidates?
- Which TPM NV/equivalent monotonic store or independent remote witness owns the trust-root high-water tuple and freshness decision?
- Which protected Windows Python runtime and dedicated verifier signing-key/agent boundary will be provisioned, and who attests its ACL, final path, dependency and key identity?
