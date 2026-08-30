# State Machines

## Maintenance command

```text
requested -> authorized -> scheduled -> executing -> succeeded
     |            |            |            +-----> failed
     |            |            +------------------> cancelled
     |            +-------------------------------> rejected
     +--------------------------------------------> expired
```

Command identity is unique per site. Authorization expires before execution, and retries return the recorded terminal result rather than repeating a power or lifecycle action.

## Release

```text
discovered -> downloaded -> verified -> preflighted -> backed_up -> staged
     -> migrated -> switched -> healthy -> committed
          |             |          |
          +-------------+----------+-> recovering -> rolled_back
                                                +-> recovery_failed
```

Verification precedes image loading and site mutation. Backup evidence precedes migration. A compatible release may restore the prior image and environment; an incompatible migration follows the approved database-restore plan.

## Subscription

```text
pending -> active -> renewal_due -> charging -> active
                         |             +------> past_due -> grace -> suspended
                         +--------------------> cancelled
```

`suspended` removes renewal-dependent support or upgrade entitlement only. Local monitoring, alarms, stored data, and safe operation remain available.

## Payment attempt

```text
created -> submitted -> provider_pending -> succeeded
                    |                   +-> failed_retryable -> submitted
                    +---------------------> failed_terminal
                    +---------------------> cancelled
```

The idempotency key is derived from subscription identity and billing period. Provider callbacks may arrive late, duplicated, or out of order; only a valid state transition can alter the invoice or entitlement.
