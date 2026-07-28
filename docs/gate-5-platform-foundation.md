# Gate 5 platform and tenant foundation

## Scope of this slice

These slices add a standard-library-only, fictional local reference for five
Gate 5 boundaries:

1. An independently started protected Platform Authority host owns the SQLite
   path, Paperclip adapter, tenant evidence store, durable artifact/learning
   store, approver-policy state, HMAC signer and verification key. The public
   package exports only principal-bound clients for this authority; none accepts
   a database path, authority ID or signing key.
2. `FictionalBuzzAdapter` accepts a typed, time-bounded context packet. Its
   authority clock enforces the deadline, and its persisted context and archive
   state can be resumed through a fresh client. It writes an immutable decision
   summary back to Paperclip, but its API cannot change task status, budget,
   dependencies, approval or closure.
3. Each worker client is bound by the authority to one exact `Principal` and an
   opaque random client token. Requests carry the token, never a caller-selected
   actor, role or tenant. A director client therefore cannot claim the named
   human approver even through Python’s base setter.
4. A durable local work queue binds immutable delivery work to one exact current
   Paperclip task version and one tenant/worker role. Renewable one-use leases,
   bounded retry, dead letters and director-owned reconciliation remain inside
   the protected authority database and cannot mutate Paperclip task state.
5. A director-only, authority-attested tenant package exports the complete logical
   Paperclip, approval, Buzz, evidence, artifact, queue and audit state. Restore
   is atomic and same-tenant into an empty target under the same authority and
   deletion ledger; protected tombstones permanently block resurrection.

These boundaries use fictional data, a local Unix socket and local SQLite only.
They make no network call and hold no service credential. The fictional
task/approval and context implementations do not yet claim authenticated
data-plane compatibility with Paperclip or Buzz.

## Installed platform compatibility admission

The next bounded slice records a non-secret, read-only contract from target host
`paperclip-511e4513` in `config/installed-platforms.json`:

- Paperclip package `2026.720.0`, its versioned root, package, lockfile, resolved
  executable, primary unit fragment and exact ordered systemd drop-in graph
  checksums, exact service account, data/workspace roots and systemd hardening;
- the private authenticated deployment health shape and absence of an active
  bootstrap invite;
- checksums for the installed health, identity, issue, approval and cost routes
  plus the installed API reference;
- the exact task, dependency, comment, approval, cost and budget method/path
  surface. Only the budget route may use the pinned cost source because that one
  route is absent from the installed API reference; and
- the current Buzz binary path, SHA-256, size and exact help-option surface for
  bounded channel context. The installed CLI has no version flag, so its binary
  hash plus command surface is the recorded identity.

`agency_os.platform_compatibility` validates the evidence, rejects secret-bearing
fields, requires private/authenticated/ready Paperclip health, preserves
Paperclip as decision authority and denies Buzz `--broadcast`. Reviewed identity
or interface drift fails closed. `scripts/verify-installed-platforms` rechecks
the package/source, executable, primary unit and every admitted drop-in hash,
systemd identity and hardening, private health response, Buzz binary and every
required target-host command option. The reviewed target currently admits no
drop-ins, so any new drop-in fails before the health or Buzz probes.

The live check is read-only. It performs no authenticated task or approval call,
creates no Buzz channel or message, and reports `real_external_writes: false`.
It proves exact installed contract admission, not authenticated lifecycle
integration, service-account separation, key custody or production readiness.

## Authority boundaries

| State | Authority in this slice |
|---|---|
| task status, dependency and closure | protected fictional Platform Authority host |
| task budget and spend | protected fictional Platform Authority host |
| task approval | exact immutable Paperclip approval bound to the active brand policy and attested and verified only inside the protected host |
| Buzz discussion context | persisted, deadline-bound fictional Buzz context; non-authoritative |
| Buzz decision | immutable summary written through the host into Paperclip-shaped state |
| evidence and provenance | protected host with a constrained evidence client |
| fictional work delivery | protected durable queue bound to exact Paperclip task version; never task authority |
| platform audit | append-only tenant-scoped events owned by the host |

Task changes use immutable versions and optimistic checksum matching. A stale
writer cannot replace a newer task version. Dependency admission reads current
Paperclip state in the same SQLite transaction. Closure needs evidence, and a
task marked `approval_required` also needs a fresh approved record bound to the
exact task checksum and the current immutable brand approver-policy revision.
Only a client already bound by the authority to a named actor in that policy may
record the approval. Each brand has one append-only policy lineage, so an
alternate policy ID cannot bypass its current revision.

The host signs the canonical approval with a domain-separated HMAC before
persistence and verifies it inside the same protected process before closure.
The host accepts no caller-supplied authority object, key, verifier or database
path, and it never derives actor, role or tenant from request fields. A newer policy revision invalidates an older approval; a
legacy, unsigned or directly inserted record impersonating even a listed actor
cannot close work. An unprovisioned, stopped or malformed host fails closed.

The standard-library HMAC and local process boundary are a fictional proof, not
production key custody or operating-system isolation. In this repository the
private host bootstrap and worker client live in one source tree and tests run
under one local service account. Production must package and run the host under
a separate authority identity that alone can access its socket directory,
SQLite path, principal catalogue and signing key, with durable key rotation and
recovery.

Buzz context, open/archive state and decisions are stored in Paperclip-shaped
persistence. A restarted Buzz adapter hydrates that exact retained context rather
than relying on process memory. Both the Buzz adapter and Paperclip write-back
boundary sample their authority clocks; expired or future-dated attempts fail,
and a caller cannot evade an elapsed deadline by backdating direct write-back.
All denials leave no decision or audit event.

## Durable artifact, learning and recovery controls

`TenantArtifactClient` is a constrained view of the same protected Platform
Authority host. Workers receive only their existing principal-bound socket client
and token; the SQLite path remains inside the host. Artifact and learning writes
reuse the role matrix, require an exact `brand_id`, preserve authenticated actor
and role provenance, and are immutable under a brand-scoped record ID. Validated,
active, evidence-backed and unexpired learning is selected with the authority
clock rather than caller time.

The store uses the authority SQLite database with WAL, full synchronous writes,
owner-only mode and pinned file/parent identity. Records survive host restart.
A separate authority-owned SQLite deletion ledger uses the same storage controls,
is bound to the exact authority ID, and is deliberately not part of the
restorable artifact database. Every source and recovery host must be explicitly
provisioned with this same ledger; a missing, wrong-authority or replaced ledger
fails host startup or artifact access closed.

Only the agency director may export or restore a tenant. The canonical export
binds every record and its original actor, role and storage time to one SHA-256.
A domain-separated HMAC held only by the protected host additionally attests the
authority ID, tenant, exact export checksum and authority export time. Restore
requires a host provisioned with that same pinned authority identity, key and
deletion ledger, the same tenant, valid record checksums and provenance, an empty
target and no authority-wide deletion tombstone. Public checksum recomputation
cannot create a valid export attestation.

Artifact/learning deletion requires the checksum of the current export while the
artifact database and deletion ledger are both write-guarded. A stale export
writes no tombstone. The protected authority durably commits the content-free
tombstone before it removes artifact/learning rows, so a crash or cleanup error
cannot reopen access or restore. A reported success also means the tenant rows
were removed, other tenants were preserved, and only checksum/count deletion
evidence remains. The tombstone denies a retained pre-deletion export on a fresh
same-authority recovery database. This is not yet full Platform Authority
offboarding: task, approval, evidence, Buzz-context and audit export/deletion,
retention timing, replicated production ledger recovery and storage-media erasure
remain separate Gate 5 work. Local IPC requests and responses are capped at 4
MiB; production bulk export requires a separately designed streaming or
protected object-transfer path.

## Durable work-delivery controls

`TenantWorkQueueClient` is another constrained view of the protected host. Only
the director can enqueue immutable work, and each item binds its tenant, assigned
worker role and the exact checksum of a current `ready` or `in_progress`
Paperclip task. A changed task version moves undelivered work to dead letter
before lease. Completion rechecks the task inside the same queue transaction:
post-lease drift dead-letters internal work, while external work stops for
destination reconciliation because its result may already be uncertain. The
queue never changes task status or represents task completion.

Lease tokens are random, returned only to the assigned actor, stored only as a
SHA-256 hash, renewable for at most 60 seconds and removed on every terminal or
waiting transition. Attempt counts and heartbeats survive host restart. Expired
internal work retries only within the item’s fixed maximum; exhaustion produces
a durable dead letter containing the immutable work, attempts and allowlisted
error classes. A director’s evidence-bound dead-letter disposition is append-only
and cannot reopen the item.

An external write that reports `UNKNOWN`, or whose lease expires without a known
result, enters `RECONCILIATION_REQUIRED`. It cannot lease again until the director
records destination evidence. A confirmed completion becomes terminal; a
confirmed absence may retry only within the original attempt bound. This queue
is not connected to a real provider, credential or gateway dispatcher in this
slice, so the external cases are fictional control-path tests and
`real_external_writes` remains false.

Tenant queue cancellation is an irreversible director-only offboarding step.
Leased or unresolved external work blocks it until destination evidence resolves
the uncertain result. Once clear, all remaining non-terminal work becomes a
lease-free `TENANT_OFFBOARDED` dead letter, terminal items and reconciliation
history remain unchanged, and a content-free evidence-linked receipt closes all
future queue mutation. Workers also lose queue read access; directors and
reviewers retain the immutable records and receipt until destructive local
offboarding begins.

The next local step prepares a content-free authority manifest containing only
per-table row counts and aggregate checksums. Full local offboarding requires the
exact current manifest and queue-cancellation receipt, then commits artifact and
authority tombstones to the separately protected deletion ledger before removing
the tenant's task versions, approver policy, approvals, Buzz context/decisions,
evidence, artifacts, queue rows and ordinary audit rows. The queue-cancellation,
artifact-deletion and authority-offboarding receipts remain readable to the
director and reviewer. Every other operation from an old client is denied as soon
as the authority tombstone commits, including after host restart and against a
fresh same-authority recovery database. A cleanup failure after that fail-closed
commit can be resumed only by repeating the exact manifest checksum and evidence
reference; a different request is denied as an immutable conflict.

This is coordinated deletion in one fictional, single-host authority. It is not
a cross-host transaction, production credential revocation, retention expiry,
backup/media erasure or full deployed platform offboarding.

## Complete logical authority export and recovery

The protected host can now produce one director-only logical package containing
all tenant rows from task versions, approver policies, approvals, Buzz contexts
and decisions, evidence, artifacts, work queue, queue cancellation and ordinary
audit. Table names and columns are fixed by code. Every stored canonical record
is revalidated before export, per-table counts and the full content are bound to
one canonical SHA-256, and the protected recovery authority attests the tenant,
authority ID, checksum and export time. Another tenant is never included.

Restore verifies the exact package shape, every row binding, the checksum and the
non-public authority attestation before writing anything. It holds the protected
deletion-ledger guard and one immediate database transaction, requires every
target table to be empty for that tenant, and inserts the complete package or
nothing. Authority-global SQLite audit sequence numbers are deliberately not
portable; ordered immutable audit records are preserved and the target assigns
safe local sequence values. Existing data for another tenant remains untouched.
The recovered task, approval, Buzz, evidence, artifact and queue boundaries stay
usable after restart under the same authority key.

An artifact-deletion or full-authority tombstone in the shared protected ledger
denies restore, including into a fresh database. Public checksum recomputation,
a wrong recovery key, a foreign tenant, a non-director and a non-empty target all
fail closed. The 4 MiB IPC limit still applies. This is a content-bearing logical
package for a fictional single-host authority, not encrypted backup storage,
streaming transfer, deployed replication, recovery-key rotation, measured RPO or
RTO, retention expiry, media erasure or multi-host recovery.

## Tenant and storage controls

- Every query includes `brand_id`; a foreign read is indistinguishable from a
  missing record.
- Task, decision, approval, evidence and audit records include `brand_id`.
- Evidence writers are explicit roles and `created_by` must equal the
  authenticated actor.
- Records are canonical JSON with a SHA-256 content checksum.
- Evidence, artifacts, learning, approvals and Buzz decisions are immutable.
  Artifact/learning provenance comes from the bound authority client. Approval
  provenance is additionally authenticated by signing material held only in the
  host process; record checksums alone are not treated as proof of origin.
- Worker clients contain only a socket path, exact bound principal and opaque
  authority token. They receive no SQLite path, deletion-ledger handle, queue
  storage or token hash, signer, verifier, policy catalogue or host bootstrap
  handle.
- Both SQLite files are owner-only, their parent cannot be group/other writable,
  and each parent and database filesystem identity is pinned. Replacement or
  symlink storage fails closed.
- Both SQLite authorities use WAL mode and full synchronous durability for this
  local reference.

## Executable denial evidence

`tests/test_platform_adapters.py` covers:

- dependency admission before and after upstream closure;
- budget overspend;
- stale task checksum;
- missing, rejected and checksum-stale approval;
- unlisted approvers, legacy unbound approvals and approver-policy revision drift;
- authority-side direct SQL fault injection impersonating a listed approver,
  every approval/attestation field changed under a recomputed content checksum,
  and a convincing same-authority-ID signature made with another key; all deny
  with no task or closure-audit mutation and the honest key absent from SQLite;
- same-ID/different-key self-provision, caller-selected principal, base-setter
  principal substitution, client socket redirection and stopped-host denial;
- cross-tenant task, Buzz, evidence, artifact and learning access;
- a non-director attempting authoritative Buzz write-back;
- expired and backdated Buzz decisions through both adapter layers;
- Buzz context and archive-state recovery across client reconstruction without
  any task-state mutation;
- evidence client reconstruction persistence, immutable conflict and wrong
  actor/role;
- artifact and validated-learning restart persistence, immutable conflict,
  role/tenant denial and authority-clock freshness;
- authority-attested export and empty-authority restore, including fully
  rechecksummed content forgery, permitted-role actor forgery, changed authority,
  wrong recovery key, foreign tenant and non-empty restore denial;
- stale-export deletion denial, durable content-free deletion evidence,
  preservation of a second tenant, and denial of a retained signed export on a
  fresh same-ID/same-key recovery database;
- missing deletion-ledger provisioning and same-path ledger replacement denial;
- internal lease renewal, restart expiry, bounded retry, durable dead letter and
  immutable evidence-bound human disposition;
- external unknown/expired-lease reconciliation before retry, with no Paperclip
  task mutation;
- queue tenant/role isolation, immutable identity and exact task-version drift
  handling both before lease and at completion, including external reconciliation
  when post-lease drift makes the destination result uncertain;
- evidence-bound tenant queue cancellation, denial while external results are
  uncertain, permanent lease/retry closure across restart, retained terminal
  history and queue-cancellation binding before artifact deletion;
- exact-manifest local authority offboarding, stale-manifest denial, content-free
  durable receipts, stored-ID tamper denial, immediate old-client denial,
  fail-closed cleanup resumption, prior-ledger migration, fresh-recovery-host
  denial and preservation of another tenant;
- complete authority export and atomic empty-target restore across task, approval,
  Buzz, evidence, artifact, queue, queue-cancellation and audit state, including
  recomputed-checksum forgery, wrong-key, wrong-role, foreign-tenant, non-empty
  target, restart and post-offboarding resurrection denial;
- tenant-scoped persistent audit; and
- replacement of the running authority database across every authority view,
  including the work queue.

## Recovery and remaining Gate 5 work

These slices prove complete logical tenant export/restore and coordinated
destructive cleanup inside one fictional local authority, not an encrypted,
replicated or complete deployed platform recovery and offboarding plan. Before
Gate 5 can complete, the project still needs:

- authenticated task, dependency, approval, budget, closure, Buzz-context and
  decision write-back tests against the admitted installed services; version,
  path, service-identity and interface evidence is now recorded read-only;
- production Paperclip approval and recovery signing, protected key custody,
  rotation and recovery under an authority service account unavailable to
  workers;
- deployed multi-host queue storage, cross-host cancellation,
  contention/failover drills and integration with the authenticated
  gateway/destination reconcilers;
- production backup, replication and disaster recovery for the protected
  deletion ledger;
- full deployed Platform Authority backup/restore, restorable task/evidence/
  Buzz/audit export, cross-store offboarding, retention timing and storage-media
  erasure drills beyond the local checksum-manifest cleanup;
- immutable audit-retention and tenant-scoped telemetry policy; and
- verified runtime bundles and fresh-session load evidence for all roles.

Production activation remains prohibited until those controls, independent
review and a separately approved production gate are complete.
