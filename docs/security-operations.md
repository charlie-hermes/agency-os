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
- Both the in-memory demonstration store and protected durable artifact authority
  check the principal's `brand_id` on every read and write. Durable workers hold
  only a principal-bound Platform Authority client, never a database path.
- Role read and write permissions are allowlisted by record type. The publishing
  operator can retrieve the public Publication Manifest but not draft or
  complete private asset packages, and tenant exports are director-only.
- Records use an artifact-type-specific primary ID and reject conflicting
  replacement. Approval Records are resolved by ID from that immutable store,
  retain authenticated writer provenance, and must match the brand-scoped
  approver identity and authority-role policy.
- Durable artifacts and learning use the protected authority SQLite database in
  WAL/full-synchronous mode with pinned owner-only storage identity. Canonical
  exports bind content and authenticated provenance to one checksum, then the
  protected authority signs that checksum with the tenant, authority ID and
  export time under a domain separated from approval signatures. Restore is
  same-tenant and empty-target only and requires the pinned authority identity,
  recovery key and separate authority-owned deletion ledger. That ledger is
  outside the restorable artifact database, bound to the exact authority ID and
  storage identity, and mandatory for every recovery host. Artifact deletion
  needs the current export checksum and commits its content-free ledger tombstone
  before artifact cleanup. The tombstone denies old signed backups even through
  a fresh same-authority recovery database. This deletion does not yet cover
  Paperclip, evidence, Buzz or audit tables, ledger replication, or media erasure.
- A separate director-only full-authority package covers task versions, approver
  policies, approvals, Buzz context and decisions, evidence, artifacts, work
  queue, queue cancellation and ordered audit records. Fixed table/column shapes,
  canonical row checks, table counts, a complete checksum and the protected
  recovery attestation are verified before one atomic empty-target restore. A
  wrong key, public checksum forgery, foreign tenant, non-empty target, artifact
  deletion or authority-offboarding tombstone denies the whole restore. This
  logical package is not encrypted backup media, streaming transfer, deployed
  replication, retention enforcement or multi-host disaster recovery.
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
- The fictional Gate 5 Platform Authority starts as a separate protected local
  process and alone receives the Paperclip SQLite path, HMAC signing key,
  signer/verifier, tenant evidence authority and exact principal catalogue.
  Worker-facing code receives only a `PlatformAuthorityClient` containing a
  private socket path, one authority-bound `Principal` and one opaque random
  token. Requests do not carry caller-selected actor, role or tenant fields; the
  host resolves identity from its token catalogue before dispatch.
- Approval creation accepts only a client bound to an actor named in the current
  immutable brand approver-policy revision. Signing and verification both stay
  inside the host. The root worker-facing package exports no Paperclip adapter,
  evidence-store constructor, signer or host bootstrap. Same-ID/different-key
  self-provision, direct constructor use, principal substitution through
  `object.__setattr__`, client socket redirection, unlisted actors, legacy
  records, policy drift and direct SQL fault injection all fail without task or
  closure-audit mutation. Redirecting a client can affect only that client; it
  cannot replace the running host or its key.
- This local HMAC/process proof is not production service-account isolation. The
  private bootstrap and clients remain in one source tree and tests use one
  local account. Production must package and run the host under a separate
  authority identity that alone owns the socket directory, database, principal
  catalogue and key, with durable rotation, recovery and client provisioning.
- The fictional Buzz boundary persists context and archive state through the
  protected host, derives decision time from authority clocks, resumes retained
  context through a fresh client, rejects expired or future-dated activity, and
  prevents elapsed-deadline bypass through backdated direct write-back without
  adding a decision or audit record.
- Audit records contain identifiers, checksums, state, and reason codes, never
  credentials or client content.
- The installed-platform manifest is produced from read-only target-host facts
  and rejects any field whose name could carry a token, credential, secret,
  password, private key or auth tag. Admission requires Paperclip's exact
  versioned paths, executable, primary service-unit bytes and the exact ordered
  set and bytes of every systemd drop-in, source hashes, reviewed route surface,
  `paperclip:paperclip` service identity, strict hardening, private authenticated
  ready health, and the pinned Buzz binary and command surface. Buzz broadcast
  and task mutation remain denied.
- The live verifier reads package files, systemd properties, private health and
  CLI help only. It performs no authenticated write and captures no secret value.

## Queue and retry semantics

The fictional local Platform Authority queue now records renewable `leased_at`,
`lease_owner`, `lease_expires_at`, attempt count and heartbeat state in protected
SQLite. Work is immutable, tenant/role-scoped and bound to the exact current
Paperclip task checksum; delivery never mutates Paperclip task state. Completion
rechecks that checksum in its queue transaction. Post-lease task drift
dead-letters internal work and sends external work to director-owned destination
reconciliation because the write result may already be uncertain. Lease expiry
is recorded and does not imply that an external write is absent. Internal
deterministic work retries only within a fixed item policy. External work in
`UNKNOWN` state, including an expired lease, requires reconciliation before
retry. Exhausted work moves to a durable dead letter with the original work,
attempts and allowlisted error classes. Its evidence-bound human disposition is
append-only and cannot reopen the item.

Queue offboarding is director-only, immutable and evidence-bound. It refuses to
close while any leased or unresolved external action could have produced an
unknown destination result. After reconciliation, it clears active internal
leases, dead-letters every non-terminal item with `TENANT_OFFBOARDED`, preserves
terminal and reconciliation history, blocks all later queue mutation and removes
worker read access. Directors and reviewers retain the content-free cancellation
receipt and original queue evidence across restart. Artifact deletion requires
and records the exact queue-cancellation receipt before deleting artifacts.

This is a single-host, fictional control path. It has no real dispatcher,
provider credential or destination query. Queue records are retained rather
than deleted. Production still requires deployed multi-host queue storage,
cross-host cancellation, coordinated full-data offboarding, contention/failover
tests and integration with the authenticated gateway and destination-specific
reconcilers.

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

The current fictional authority implements the first three local ordering steps
and one bounded deletion proof. Evidence-bound queue cancellation reconciles
uncertain external results and permanently stops worker delivery. Before deletion,
a director can export one complete authority-attested logical package containing
the tenant's task, approval, Buzz, evidence, artifact, queue and audit state. A
director then prepares an exact checksum/count manifest and can commit protected
artifact and authority tombstones before deleting that tenant's local content.
Old clients fail closed immediately, an interrupted cleanup resumes only with
the exact same manifest and evidence, and the tombstone denies the retained full
package even through a fresh same-authority recovery database. Content-free
queue, artifact and authority receipts remain.

This is not a deployed, encrypted or replicated backup system. The full sequence
above remains incomplete until real credentials and grants are revoked, retention
is owner-approved, deployed stores and backups are coordinated across hosts,
protected-ledger replication is proven, recovery objectives are drilled, and
storage-media erasure is verified.

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

The fictional single-host Platform Authority does not activate those values. It
requires a director to record an explicit evidence-bound value, retains every
policy revision, refuses to shorten the current minimum, and expires audit rows
only from an exact current manifest after the configured window. Its telemetry
is local, tenant-scoped and content-free. This proves the control shape and
failure semantics only; it is not production monitoring, a legal-records
schedule, an external telemetry pipeline, multi-host expiration, backup expiry
or media erasure.

## Production promotion blockers

- deploy the durable action ledger on storage suited to the worker topology and
  prove its access controls, backup, restore, contention, and reconciliation;
- verified 12-agent runtime bundles and fresh-session load tests;
- authenticated lifecycle adapters verified against the admitted Paperclip and
  Buzz services (only the exact read-only installed contract is admitted; the
  current typed lifecycle adapters remain local fictional references);
- deployed row-level or physically isolated tenant storage with backup,
  restore and offboarding evidence (the current evidence authority is local
  SQLite only);
- credential broker and deny-by-default egress;
- persistent capability admission, runtime identity binding, and drift
  suspension across deployed workers;
- deployed multi-host queue storage, cross-host cancellation and integration
  with real gateway/destination reconciliation (the current queue is
  fictional/local);
- owner-approved production audit retention, external tenant-scoped telemetry
  and deployed expiry scheduling beyond the fictional single-host reference;
- backup, restore, and destructive offboarding exercises;
- current provider/account eligibility and data-handling review;
- full acceptance matrix against the deployed candidate; and
- independent Platform Assurance `PASS` with no open P0/P1 finding.
