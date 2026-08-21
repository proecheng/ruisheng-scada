# Security Controls

## Remote maintenance

- Use a dedicated non-interactive operations identity with key-only authentication and Tailscale source restrictions.
- Separate Docker lifecycle access from the administrative host-power broker.
- Require action-specific authorization, site identity, reason, expiry, nonce, and expected current state.
- Apply a site-wide maintenance lock so shutdown, patch, backup, and upgrade cannot overlap.
- Redact secrets, authorization headers, query tokens, environment values, and customer data from collected logs.
- Enable Windows OpenSSH event logging and PowerShell transcription in a protected audit directory with bounded retention.

## Release supply chain

- Sign the canonical manifest with a centrally protected Ed25519 release key; pin key id and public key at the site.
- Verify signature, manifest schema, SHA-256, image id, source commit, target platform, release channel, and expiry before mutation.
- Preserve an immutable deployment record containing previous and requested identities, backup evidence, checks, transitions, and recovery result.
- Refuse mutable tags, unknown signing keys, rollback below the configured minimum safe version, and concurrent maintenance.

## Billing and entitlements

- Integrate an official payment-provider API and certificate flow; do not reuse the current V2-style stub as an API v3 client.
- Store merchant keys and mandate tokens centrally in a secret manager; never copy them into Docker images, site env files, logs, or target storage.
- Authenticate and decrypt callbacks before parsing business fields; deduplicate provider event identity and enforce amount, currency, merchant, invoice, and state checks.
- Use a separate entitlement signing key. Grants contain site, customer, plan, feature set, issued/expiry/grace times, serial, and key id.
- Notify before renewal and on every success, retry, failure, cancellation, refund, or entitlement change.

## Safety invariants

- Billing state cannot call maintenance, host-power, GW lifecycle, alarm, or device-control interfaces.
- Maintenance state cannot create invoices, payment attempts, refunds, mandates, or entitlements.
- Loss of central connectivity enters bounded entitlement grace and cannot disable local safety-critical operation.
- Destructive volume deletion, unapproved device control, and unsigned code execution are denied rather than converted into confirmation prompts.
