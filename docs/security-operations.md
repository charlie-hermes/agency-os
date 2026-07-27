# Security and operations contract

## Phase 0/1 enforcement

- The gateway no longer accepts a worker-supplied `Principal`. A trusted local
  supervisor observes a pinned runtime ID, operating-system user, executable
  checksum, and instance nonce. The runtime authority derives the immutable
  `Principal` from that enrollment after authenticating a signed, short-lived,
  one-use assertion. Missing, malformed, future, expired, replayed, unregistered,
  or changed-runtime assertions fail before capability or action resolution.
- The tenant store checks the principal's `brand_id` on every read and write.
- Role read and write permissions are allowlisted by record type. The publishing
  operator can retrieve the public Publication Manifest but not draft or
  complete private asset packages, and tenant snapshots are director-only.
- Records use an artifact-type-specific primary ID and reject conflicting
  replacement. Approval Records are resolved by ID from that immutable store,
  retain authenticated writer provenance, and must match the brand-scoped
  approver identity and authority-role policy.
- Public fields and internal notes are separate values; only public fields
  cross the publication adapter.
- Capability grants are immutable, brand-scoped records issued through an
  authoritative registry by the authenticated agency director. A grant binds
  one actor and role to one environment, destination, operation, action class,
  and data class for a bounded time. Missing, suspended, expired, future,
  checksum-invalid, or mismatched grants fail closed. The gateway resolves the
  grant by ID rather than accepting caller-supplied grant content. After action
  reservation, the authority samples a trusted dispatch-time clock and
  serializes final active, checksum, and capability-window checks with adapter
  invocation. The same sampled time revalidates approval expiry and the schedule
  window. The fictional in-memory implementation coordinates threads. The
  durable SQLite implementation persists grants and suspensions and orders
  dispatch with suspension across processes on one host. Its database must be a
  non-symlink regular file owned by the service account with mode `0600`. Its
  immediate parent must be a non-symlink directory owned by that account with no
  group or other write permission. Both identities and permissions are pinned
  at startup and revalidated around every new connection. The database must not
  be replaced while the service is running or shared over a network filesystem.
  Production still requires topology-appropriate shared authority plus
  enforcement at the credential and egress boundary across deployed workers.
- The action gateway also checks role, brand, environment, destination,
  operation, approval expiry, manifest checksum, artifact checksum, and
  schedule. Because Phase 0/1 has no typed condition evaluator, an Approval
  Record with missing, malformed, or non-empty `conditions` fails closed before
  adapter dispatch; only an explicit empty array is currently publishable.
- The action gateway requires an explicit ledger. The fictional demonstration
  uses a thread-safe in-memory ledger; the durable SQLite implementation uses a
  unique `(brand_id, idempotency_key)` and an atomic transaction shared by
  gateway instances and local worker processes. Intent is saved before adapter
  dispatch, exact replay
  returns the saved receipt, conflicting reuse is denied, and `REQUESTED` or
  `UNKNOWN` states survive restarts and require reconciliation. A multi-host
  deployment must provide the same ledger contract through a database designed
  and tested for that topology; a SQLite file must not be shared over a network
  filesystem. The SQLite database follows the same pinned file and parent
  ownership, mode, symlink, and per-connection revalidation rules as the
  capability authority.
- The fictional credential broker binds one mock credential to the exact live
  capability checksum, authenticated actor, role, brand, environment,
  destination and operation. It creates a one-use lease of at most 30 seconds
  and releases the mock value only inside the adapter call. The adapter rejects
  direct calls without a broker lease. Destination-to-endpoint mapping is
  allowlisted and this candidate refuses every endpoint except `mock://`.
- The only Phase 0/1 destination is a local mock. Real egress is explicitly
  denied. The supervisor, in-memory runtime authority and fictional credential
  broker are reference controls, not claims of production VM enforcement.
- Audit records contain identifiers, checksums, state, and reason codes, never
  credentials or client content.

## Queue and retry semantics

A production queue must use renewable leases with `leased_at`, `lease_owner`,
`lease_expires_at`, attempt count, and heartbeat. Expired leases become visible
as stale; they do not imply that an external write is absent. Internal
deterministic work may retry within a bounded policy. External writes in
`UNKNOWN` state require destination reconciliation before retry. Exhausted work
moves to a dead-letter queue with the original task, attempts, error classes,
and human disposition. No dead-letter item may silently reopen itself.

## Credential lifecycle

Production credentials must be issued by a broker to authenticated workloads
for one brand, provider audience, account, environment, role, and capability.
Values must not enter source, prompts, artifacts, Buzz, Paperclip comments, or
ordinary telemetry. Rotation overlaps must be bounded and audited. Suspension
revokes the capability and credential together. Offboarding revokes tokens,
webhooks, app grants, signing material, and egress before tenant deletion.

## Tenant offboarding

Offboarding requires a human-approved plan that:

1. blocks new work and external dispatch;
2. reconciles scheduled, processing, and unknown actions;
3. exports the required audit/evidence package;
4. revokes credentials, webhooks, and provider grants;
5. deletes tenant operational, learning, cache, and diagnostic data according
   to retention policy;
6. verifies backups expire or are cryptographically erased as promised; and
7. records deletion evidence without retaining deleted content.

## Audit and service objectives

For production, the owner must choose and test values before activation. The
minimum proposed baseline is:

| Control | Proposed target | Activation evidence |
|---|---:|---|
| Operational audit retention | 400 days | policy plus tenant deletion test |
| Diagnostic content retention | off by default; maximum 7 days when approved | expiry test |
| RPO | 15 minutes | restore drill with measured loss |
| RTO | 4 hours | timed restore drill |
| Core control-plane availability SLO | 99.9% monthly | monitored definition |
| External action reconciliation | 100% of `UNKNOWN` states | incident/query evidence |

These are proposed defaults, not claims about the current implementation.
Production remains blocked until the human owner approves the targets and a
real persistent deployment meets them.

## Production promotion blockers

- deploy the durable action ledger on storage suited to the worker topology and
  prove its access controls, backup, restore, contention, and reconciliation;
- verified 12-agent runtime bundles and fresh-session load tests;
- real Paperclip and typed Buzz adapter evidence;
- persistent row-level or physically isolated tenant storage;
- credential broker and deny-by-default egress;
- persistent capability admission, runtime identity binding, and drift
  suspension across deployed workers;
- queue lease, dead-letter, cancellation, and reconciliation implementation;
- immutable audit retention and tenant-scoped telemetry;
- backup, restore, and destructive offboarding exercises;
- current provider/account eligibility and data-handling review;
- full acceptance matrix against the deployed candidate; and
- independent Platform Assurance `PASS` with no open P0/P1 finding.
