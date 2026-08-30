---
id: SPEC-remote-maintenance-upgrades-subscriptions
companions:
  - architecture.md
  - state-machines.md
  - security-controls.md
  - ../../../REMOTE_DEBUG.md
  - ../spec-plan-5-customer-deployment-acceptance/deployment-contract.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability only.

# Remote Maintenance, Upgrades, and Subscriptions

## Why

Operators need to diagnose, stop, patch, and upgrade a remote Windows installation without opening application ports or visiting the site, while customers need predictable subscription renewal. These capabilities affect an industrial monitoring system, so host control, software supply chain, and money movement must remain independently authorized and recoverable.

## Capabilities

- id: CAP-1
  intent: An authorized operator can establish time-bounded remote diagnostic access and collect service health and sanitized logs.
  success: An approved operator can diagnose all application services through Tailscale while application, database, and cache ports remain unavailable from unapproved networks.

- id: CAP-2
  intent: An authorized operator can stop, start, or restart the application stack without deleting persistent data.
  success: Each operation records actor, reason, target, timestamps, result, and service state; stop/start recovery preserves database, Redis, and GW WAL volumes.

- id: CAP-3
  intent: A separately authorized operator can safely restart or shut down the Windows host during a maintenance window.
  success: Host power operations require an explicit reason and confirmation, reject conflicting maintenance work, permit cancellation during a delay, and leave an audit record before shutdown.

- id: CAP-4
  intent: An operator can deploy an immutable patch to one application service and automatically recover the previous service on failure.
  success: Unsigned, corrupt, wrong-platform, wrong-commit, non-loopback, or unhealthy patches are rejected before commitment; a post-switch failure restores the prior image and site environment.

- id: CAP-5
  intent: A site can move to an approved full release through a policy-controlled unattended upgrade.
  success: The updater verifies release identity, maintenance policy, resources, backup evidence, schema compatibility, migration, health, and recovery before recording the release as committed.

- id: CAP-6
  intent: An administrator can manage plans, subscriptions, billing cycles, grace periods, cancellation, and renewal notices.
  success: Every billing period produces at most one invoice and an auditable subscription transition, including offline and overdue periods.

- id: CAP-7
  intent: A customer can authorize and revoke recurring payment through an approved payment-provider mandate.
  success: A renewal is charged at most once per subscription and billing period, provider callbacks are authenticated and replay-safe, and revocation prevents future charge attempts.

- id: CAP-8
  intent: A target installation can consume signed subscription entitlements without possessing payment credentials.
  success: The target accepts only current, correctly scoped, signed entitlements and applies grace policy without disabling local monitoring, alarms, data access, or safety functions.

- id: CAP-9
  intent: Operations and finance personnel can investigate every command, deployment, entitlement, and payment transition without exposing secrets.
  success: Correlated append-only audit records reconstruct actor or system identity, authorization, inputs, immutable artifact or billing identity, state transitions, and outcome.

## Constraints

- Remote administration uses Tailscale and key-only OpenSSH; no application, API, database, Redis, or management port may be exposed to an unapproved network.
- Host maintenance, release signing, and payment authorization use separate identities, credentials, roles, stores, and audit trails.
- The target stores verification keys and signed entitlements only; release-signing private keys, merchant private keys, payment mandates, and provider secrets remain central.
- Non-payment never shuts down Windows, stops GW collection, suppresses alarms, deletes data, or issues device-control commands.
- Application stop and host power actions never delete Docker volumes; host power actions require stronger authorization than application lifecycle actions.
- Automatic release rollback is permitted only for schema-compatible releases; incompatible migrations require an approved database-restore path.
- Real payment, production network changes, device control, and production rollout remain blocked until their existing acceptance gates and external approvals pass.
- Every remote command, artifact, entitlement, and provider callback has an immutable idempotency identity and bounded replay window.

## Non-goals

- Remote emergency stopping or controlling industrial field devices.
- Circumventing payment-provider merchant qualification, mandate consent, cancellation, refund, invoice, or regulatory requirements.
- Treating SHA-256 alone as publisher authenticity or treating an application-image rollback as database recovery.
- Installing a general-purpose public remote shell, internet-facing management API, or privileged CI runner on the target.

## Success signal

- On an approved Windows test site, operators complete audited diagnostics, safe application and host lifecycle operations, corrupt-update rejection, successful patch/full upgrade, interrupted-upgrade recovery, duplicate-renewal suppression, mandate revocation, and offline-grace demonstrations without opening protected ports, losing data, double charging, or interrupting local safety-critical monitoring because of billing state.

## Assumptions

- Subscription fees represent software or support service entitlement, not the existing per-device recharge balance.
- Initial delivery targets one Windows laptop but assigns stable site identities so the control plane can later manage multiple sites.
- The first billing release creates renewal orders and notifications with customer-confirmed payment; unattended charging follows only after an approved recurring-payment mandate product is available.

## Open Questions

- Which payment provider and recurring-payment product is approved, and does the merchant account already hold the required qualification?
- What are the plans, prices, billing cycles, advance-notice period, retry schedule, grace period, tax-invoice rules, and refund policy?
- Which named roles may stop applications, restart the host, shut down the host, approve releases, and view financial audit records?
- Where will the always-on central control and billing services run, and what availability and data-residency requirements apply?
