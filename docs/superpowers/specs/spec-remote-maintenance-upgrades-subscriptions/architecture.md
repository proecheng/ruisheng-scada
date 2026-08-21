# Architecture

## Trust domains

```text
Operator workstation --Tailscale/OpenSSH--> Target maintenance boundary --> Docker/Windows
Release controller  --signed manifest----> Target updater              --> staged release
Billing service <--> Payment provider --> Entitlement signer --signed grant--> Target app
```

The maintenance boundary accepts operator commands but cannot sign releases or charge customers. The release controller can select and sign software but cannot issue host-power commands. The billing service owns mandates and invoices but can only issue entitlement state; it cannot reach SSH or Docker.

## Target components

| Component | Privilege | Responsibility |
|---|---|---|
| OpenSSH through Tailscale | Dedicated operations account | Key-only transport and loopback forwarding |
| Maintenance command | Docker access, no payment access | Health, sanitized logs, application lifecycle, conflict lock, audit |
| Host power broker | Narrow administrative task | Delayed restart/shutdown after stronger authorization |
| Updater | Docker and release directories | Verify, preflight, backup, stage, switch, health, recover |
| Application | No host maintenance privilege | Validate signed entitlement and expose business status |

The first implementation remains push-based from the operator workstation. A later target-side scheduled updater may pull desired state, but it must execute the same signed state machine and must not become a general-purpose CI runner.

## Central components

| Component | Responsibility |
|---|---|
| Release controller | Channel policy, signed immutable manifests, rollout approvals, deployment history |
| Billing service | Plans, subscriptions, invoices, mandates, attempts, provider callbacks, refunds |
| Entitlement signer | Converts committed billing state into short, signed, site-scoped grants |
| Audit store | Append-only correlated events with role-separated access and retention |

Payment and release private keys use separate secret stores and rotation procedures. The target pins corresponding public keys and refuses unknown key identifiers.

## Delivery sequence

1. Harden remote identity and implement safe application/host lifecycle commands.
2. Sign existing patch artifacts and add deployment records.
3. Implement full-release desired state, backup, migration compatibility, and recovery.
4. Implement plan/subscription/invoice state with simulated provider callbacks.
5. Replace the payment stub with an approved provider integration and customer-confirmed renewal.
6. Add unattended charges only after mandate qualification and consent acceptance tests pass.
