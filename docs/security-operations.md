# Security and operations contract

## Phase 0/1 enforcement

- Before a worker receives any endpoint, the fictional authority observes that
  already-running worker's PID from outside the worker, derives its service UID,
  executable checksum and PID-bound process-start identity from `/proc`, and
  freezes the exact observation-to-`Principal` enrollment. It then starts a
  separate protected host containing that catalogue, the assertion signer,
  action gateway, capability authority, credential broker and publisher. The
  fictional worker starts with Python's clean `spawn` method, rather than
  inheriting the authority process memory through `fork`. It receives only an
  `ActionGatewayClient` holding a Unix-socket path; the
  public worker factory cannot create a host and accepts no principal, role,
  brand, runtime ID, observation or time. On every request the host derives the
  connecting PID and UID from Linux `SO_PEERCRED`, re-derives executable and
  process-start facts from `/proc`, and requires an exact catalogue match before
  a signed one-use assertion maps to the principal. The worker has no gateway,
  broker, catalogue, publisher or signer object to replace, including through
  Python's base `object.__setattr__`. The authority accepts the completed
  publication receipt from the protected host's control channel, not from the
  worker's return value. Missing, malformed, future, overlong,
  expired, replayed, unregistered, another-process or changed-runtime assertions
  fail before capability or action resolution. The local socket lives in a
  private directory and has mode `0600`.
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
  reservation, the authority checks once before adapter entry. At credential
  consumption it re-reads active state and checksum, resamples the trusted clock,
  and revalidates capability, approval and schedule windows. A suspension that
  completes before credential consumption wins; successful final authorization
  is then held through adapter return. The fictional in-memory implementation
  coordinates threads. The
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
  destination and operation. It rejects configuration above a hard 30-second
  lease maximum, creates a one-use lease, and releases the mock value only after
  the final authorization guard succeeds inside the adapter call. The adapter rejects
  direct calls without a broker lease. Destination-to-endpoint mapping is
  allowlisted and this candidate refuses every endpoint except `mock://`.
- The only Phase 0/1 destination is a local mock. Real egress is explicitly
  denied. The separate Linux host, local authority and fictional credential
  broker are reference controls, not claims of production VM enforcement. This
  repository demonstrates the process boundary with one fictional enrollment
  under the current local service account. Production still requires the host
  and persistent identity catalogue to run under an authority-owned service
  identity unavailable to workers, plus separately provisioned worker UIDs or
  equivalent workload identities. Gate 4 supports one worker identity per
  operating-system process; several roles sharing a process are explicitly
  unsupported because peer credentials cannot distinguish them.
- The complete Gate 4 repository verifier is Linux-only. It fails before tests
  unless Linux, `/proc`, and `SO_PEERCRED` are available, and the same command is
  required in Ubuntu GitHub Actions. macOS and Windows are not supported
  validation hosts for this gate.
- The fictional Gate 5 Paperclip boundary accepts a task approval only from an
  actor named in the current immutable brand approver-policy revision. Each
  brand has one append-only policy lineage. Approval creation and task closure
  both re-read that active revision; alternate IDs, unlisted actors, legacy
  unbound records and revision drift fail without task or closure-audit
  mutation. The fictional Buzz boundary persists context and archive state in
  Paperclip-shaped storage, derives decision time from authority clocks, resumes
  retained context after adapter restart, rejects expired or future-dated
  activity, and prevents elapsed-deadline bypass through backdated direct
  write-back without adding a decision or audit record.
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
- adapters verified against the installed Paperclip and Buzz services (the
  current typed adapters are local fictional references only);
- deployed row-level or physically isolated tenant storage with backup,
  restore and offboarding evidence (the current evidence authority is local
  SQLite only);
- credential broker and deny-by-default egress;
- persistent capability admission, runtime identity binding, and drift
  suspension across deployed workers;
- queue lease, dead-letter, cancellation, and reconciliation implementation;
- immutable audit retention and tenant-scoped telemetry;
- backup, restore, and destructive offboarding exercises;
- current provider/account eligibility and data-handling review;
- full acceptance matrix against the deployed candidate; and
- independent Platform Assurance `PASS` with no open P0/P1 finding.
