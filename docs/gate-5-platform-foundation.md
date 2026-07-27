# Gate 5 platform and tenant foundation

## Scope of this slice

This slice adds a standard-library-only, fictional local reference for three
Gate 5 boundaries:

1. `FictionalPaperclipAdapter` owns versioned task state, dependency admission,
   budget consumption, a versioned brand approver policy, exact task approvals,
   evidence-bound closure, Buzz decision summaries and tenant-scoped audit
   events.
2. `FictionalBuzzAdapter` accepts a typed, time-bounded context packet. Its
   authority clock enforces the deadline, and its persisted context and archive
   state can be resumed after adapter restart. It writes an immutable decision
   summary back to Paperclip, but its API cannot change task status, budget,
   dependencies, approval or closure.
3. `SQLiteTenantEvidenceStore` retains immutable, cited evidence by
   `brand_id` and `paperclip_issue_id` across restarts.

All three use fictional data and local SQLite only. They make no network call,
hold no service credential and do not claim compatibility with an installed
Paperclip or Buzz version.

## Authority boundaries

| State | Authority in this slice |
|---|---|
| task status, dependency and closure | fictional Paperclip adapter |
| task budget and spend | fictional Paperclip adapter |
| task approval | exact immutable Paperclip approval record bound to the active brand approver-policy revision |
| Buzz discussion context | persisted, deadline-bound fictional Buzz context; non-authoritative |
| Buzz decision | immutable summary written into Paperclip |
| evidence and provenance | tenant evidence store |
| platform audit | append-only tenant-scoped SQLite events |

Task changes use immutable versions and optimistic checksum matching. A stale
writer cannot replace a newer task version. Dependency admission reads current
Paperclip state in the same SQLite transaction. Closure needs evidence, and a
task marked `approval_required` also needs a fresh approved record bound to the
exact task checksum and the current immutable brand approver-policy revision.
Only a named actor in that policy may record the approval. Each brand has one
append-only policy lineage, so an alternate policy ID cannot bypass its current
revision. A newer revision invalidates an older approval, and a legacy or forged
approval outside the policy cannot close work.

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
- Evidence, approvals and Buzz decisions are immutable.
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
- cross-tenant task, Buzz and evidence access;
- a non-director attempting authoritative Buzz write-back;
- expired and backdated Buzz decisions through both adapter layers;
- Buzz context and archive-state recovery across adapter restart without any
  task-state mutation;
- evidence restart persistence, immutable conflict and wrong actor/role;
- tenant-scoped persistent audit; and
- replacement of the running authority database.

## Recovery and remaining Gate 5 work

This slice proves restart persistence, not a complete recovery plan. Before
Gate 5 can complete, the project still needs:

- adapters tested against the actual installed Paperclip and Buzz versions,
  with their host paths and service identities recorded;
- persistent artifact and learning authorities integrated with this evidence
  boundary;
- queue leases, retries, dead-letter handling and reconciliation;
- backup, restore, retention, export and destructive offboarding drills;
- immutable audit-retention and tenant-scoped telemetry policy; and
- verified runtime bundles and fresh-session load evidence for all roles.

Production activation remains prohibited until those controls, independent
review and a separately approved production gate are complete.
