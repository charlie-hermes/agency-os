# Gate 5 platform and tenant foundation

## Scope of this slice

This slice adds a standard-library-only, fictional local reference for three
Gate 5 boundaries:

1. An independently started protected Platform Authority host owns the SQLite
   path, Paperclip adapter, tenant evidence store, approver-policy state, HMAC
   signer and verification key. The public package exports only
   `PlatformAuthorityClient` and `TenantEvidenceClient` for this authority; neither accepts a
   database path, authority ID or signing key.
2. `FictionalBuzzAdapter` accepts a typed, time-bounded context packet. Its
   authority clock enforces the deadline, and its persisted context and archive
   state can be resumed through a fresh client. It writes an immutable decision
   summary back to Paperclip, but its API cannot change task status, budget,
   dependencies, approval or closure.
3. Each worker client is bound by the authority to one exact `Principal` and an
   opaque random client token. Requests carry the token, never a caller-selected
   actor, role or tenant. A director client therefore cannot claim the named
   human approver even through Python’s base setter.

These boundaries use fictional data, a local Unix socket and local SQLite only.
They make no network call and hold no service credential. The fictional
task/approval and context implementations do not yet claim authenticated
data-plane compatibility with Paperclip or Buzz.

## Installed platform compatibility admission

The next bounded slice records a non-secret, read-only contract from target host
`paperclip-511e4513` in `config/installed-platforms.json`:

- Paperclip package `2026.720.0`, its versioned root, package and lockfile
  checksums, exact service account, executable, data/workspace roots and systemd
  hardening;
- the private authenticated deployment health shape and absence of an active
  bootstrap invite;
- checksums for the installed health, identity, issue, approval and cost routes plus
  the installed API reference;
- the task, dependency, comment, approval, cost and budget method/path surface
  needed by the future authenticated adapter; and
- the current Buzz binary path, SHA-256, size and exact help-option surface for
  bounded channel context. The installed CLI has no version flag, so its binary
  hash plus command surface is the recorded identity.

`agency_os.platform_compatibility` validates the evidence, rejects secret-bearing
fields, requires private/authenticated/ready Paperclip health, preserves
Paperclip as decision authority and denies Buzz `--broadcast`. Reviewed identity
or interface drift fails closed. `scripts/verify-installed-platforms` rechecks
the package/source hashes, systemd identity and hardening, private health response,
Buzz binary and every required command option on the target host.

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

## Tenant and storage controls

- Every query includes `brand_id`; a foreign read is indistinguishable from a
  missing record.
- Task, decision, approval, evidence and audit records include `brand_id`.
- Evidence writers are explicit roles and `created_by` must equal the
  authenticated actor.
- Records are canonical JSON with a SHA-256 content checksum.
- Evidence, approvals and Buzz decisions are immutable. Approval provenance is
  additionally authenticated by signing material held only in the host process;
  record checksums alone are not treated as proof of origin.
- Worker clients contain only a socket path, exact bound principal and opaque
  token. They receive no SQLite path, signer, verifier, policy catalogue or host
  bootstrap handle.
- The SQLite file is owner-only, its parent cannot be group/other writable, and
  both the parent and database filesystem identities are pinned. Replacement or
  symlink storage fails closed.
- SQLite uses WAL mode and full synchronous durability for this local reference.

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
- cross-tenant task, Buzz and evidence access;
- a non-director attempting authoritative Buzz write-back;
- expired and backdated Buzz decisions through both adapter layers;
- Buzz context and archive-state recovery across client reconstruction without
  any task-state mutation;
- evidence client reconstruction persistence, immutable conflict and wrong
  actor/role;
- tenant-scoped persistent audit; and
- replacement of the running authority database.

## Recovery and remaining Gate 5 work

This slice proves restart persistence, not a complete recovery plan. Before
Gate 5 can complete, the project still needs:

- authenticated task, dependency, approval, budget, closure, Buzz-context and
  decision write-back tests against the admitted installed services; version,
  path, service-identity and interface evidence is now recorded read-only;
- production Paperclip approval signing, protected key custody, rotation and
  recovery under an authority service account unavailable to workers;
- persistent artifact and learning authorities integrated with this evidence
  boundary;
- queue leases, retries, dead-letter handling and reconciliation;
- backup, restore, retention, export and destructive offboarding drills;
- immutable audit-retention and tenant-scoped telemetry policy; and
- verified runtime bundles and fresh-session load evidence for all roles.

Production activation remains prohibited until those controls, independent
review and a separately approved production gate are complete.
