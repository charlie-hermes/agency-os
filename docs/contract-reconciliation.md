# Agency OS contract reconciliation

**Status:** authoritative for this repository
**Scope:** Phase 0/1 specification and fictional reference implementation
**Supersedes:** conflicting role, artifact, learning, and repository details in
the preserved implementation blueprint

## Authority hierarchy

Paperclip owns work state, dependencies, budgets, approvals, and closure.
Hermes performs executive orchestration. Buzz carries bounded discussion only.
Specialist runtimes act through typed artifacts and admitted capabilities.
Humans retain configured business, infrastructure, credential, and external
action authority.

## Canonical role catalogue

The platform has exactly 12 roles:

1. Hermes Agency Director
2. Codex Technical Implementation Specialist
3. Platform Assurance Reviewer
4. Brand and Brief Steward
5. Search and Content Strategist
6. Content Producer
7. Search and Answer Optimiser
8. Visual and Creative Specialist
9. Editorial Integrity QA
10. Social Amplifier
11. Publishing Operator
12. Growth Intelligence Analyst

The Technical Implementation Specialist is a build and repair role. The
Platform Assurance Reviewer is an independent release and activation gate.
Neither becomes a business approver. All 12 role pairs live under `roles/`.

## Canonical serialization and checksums

Every checksum in this repository uses UTF-8 JSON with:

- lexicographically sorted object keys;
- no insignificant whitespace;
- Unicode emitted directly rather than ASCII-escaped;
- arrays kept in their declared order;
- duplicate object keys prohibited by construction;
- non-finite numbers prohibited; and
- the checksum field itself omitted from the bytes it authenticates.

The digest format is `sha256:<64 lowercase hexadecimal characters>`. A material
change produces a new checksum, QA verdict, and approval where applicable.

## Asset lifecycle

The lifecycle has four distinct, versioned objects:

| Object | Owner | Required binding |
|---|---|---|
| Draft Asset Package | Content Producer | approved brief, public body, private notes, claims, sources |
| Complete Asset Package | Search and Answer Optimiser | exact draft checksum, optimised public fields, metadata, links, structured data |
| QA-Passed Asset Package | Editorial Integrity QA gate | exact complete-package checksum and PASS verdict |
| Publication Manifest | Agency Director / authorised control plane | brand, exact QA-passed checksum, every child checksum, destination account, environment, public fields, operation, schedule window, deterministic transformation version |

Formal schemas are in `schemas/`. Public content is always in `public_fields`;
internal notes are always in `internal_notes`. The action gateway accepts only
`public_fields`.

An Approval Record binds the exact Publication Manifest checksum and repeats
the security-critical brand, artifact, destination, operation, environment, and
schedule bindings. A Publication Receipt binds the approval, manifest,
idempotency key, adapter version, external state, and reconciliation evidence.

## Learning lifecycle

The durable learning objects are:

- `LearningContextManifest` — exact active records considered for a task;
- `FailureObservation` — a typed, evidence-linked failure or avoidable rework;
- `CandidateLearning` — a specialist proposal that has no activation authority;
- `LearningRecord` — a Director-disposed, versioned record with evidence,
  confidence, limitations, freshness, expiry, and supersession lineage.

Only `active` and validated records may enter a context manifest. Brand-only
records require an exact `brand_id` match. Agency-shared records require
explicit approval and a sanitisation attestation. Expired, superseded,
unvalidated, corrupted, or wrong-brand records fail closed. A known failed
action may not be repeated unchanged without new evidence and an authorised
exception.

## Capability and action boundary

Tool discovery and credential availability confer no authority. Every action
requires an active capability record for the authenticated role, brand,
environment, destination, operation, and data/action class. The gateway derives
the principal from runtime state and does not accept an agent-supplied identity.
Capability content is resolved by ID from an authoritative registry; immutable
grant bindings and mutable suspension state are not accepted from the caller.

Before dispatch, it revalidates capability, approval, checksum, destination,
schedule, and idempotency. The fictional in-process authority orders suspension
and adapter invocation under one dispatch lock, so whichever acquires authority
first defines the boundary. An allow decision is single-use for the bound
request. Unknown or partial external results must be reconciled before retry.

## Phase boundary

Phase 0/1 proves schemas, deterministic checksums, tenant storage, role
separation, approval binding, idempotency, recovery fixtures, and a fictional
vertical slice. Production activation remains blocked until the runtime bundle,
credential broker, real Paperclip/Buzz adapters, persistent tenant store,
network/egress controls, backups, restore exercise, observability, and
independent assurance evidence in `docs/security-operations.md` are complete.
