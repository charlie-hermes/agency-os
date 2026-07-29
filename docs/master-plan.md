# Agency OS master plan

## Purpose and authority

This is the living delivery map for the complete Agency OS described by:

1. `docs/reference/implementation-blueprint.md`;
2. `roles/CODEX-SUPER-AGENT-ACTIVATION-BRIEF.md`;
3. `docs/contract-reconciliation.md`; and
4. `docs/security-operations.md`.

It does not replace those contracts. Where documents differ, the authority order
in `docs/contract-reconciliation.md` controls. Every implementation change must
name the roadmap gate it advances and must not weaken an already passed gate.

Paperclip remains authoritative for tasks, approvals, dependencies, budgets and
closure. Buzz is a collaboration surface, not a second source of truth. Real
credentials, live client data, purchases and external writes remain prohibited
until a separately approved production gate explicitly permits them.

## Product outcome

Agency OS is one tenant-isolated, evidence-led marketing production system with
two products:

- **Search Authority Core** — onboarding, intake, research, strategy, creation,
  search/answer optimisation, independent QA, human approval, publication,
  validation, measurement and controlled learning.
- **Search Authority + Social Amplifier** — the Core workflow plus an optional
  post-approval branch for channel-native social variants, social QA, approval,
  publishing, validation and measurement.

The system has 12 specialist role contracts. Hermes directs operations,
Paperclip owns work state, Buzz supports focused discussion, Codex implements
technical changes, and humans retain business, approval, credential,
infrastructure, purchase and public-publication authority.

## Non-negotiable end-to-end controls

- Every business record and action is bound to one `brand_id`.
- Cross-brand retrieval, discussion, credentials, publishing and measurement
  fail closed.
- Public fields are structurally separated from private notes.
- Claims and metrics carry retained evidence and provenance.
- Producers cannot approve their own work.
- QA, approvals and publication bind to exact canonical checksums.
- Approval also binds destination, operation, environment and time window.
- Worker identity is derived from a trusted runtime boundary, not caller input.
- Credentials are short-lived, least-privilege and released only through a
  broker for the exact approved destination and operation.
- External egress is allowlisted; direct adapter or network bypass is denied.
- Idempotency and reconciliation prevent duplicate or blind repeat actions.
- Learning is same-brand, validated, evidence-backed, freshness-limited and
  supersedable.
- Operator views reflect Paperclip; they never become an alternate authority.

## Gate rules

Each gate has an entry condition, deliverable and release evidence. A gate is
complete only when:

1. the deliverable is versioned;
2. its full repository gate passes at the reported commit;
3. adversarial tests cover the nearest unsafe alternatives;
4. rollback or recovery behavior is documented where state changes;
5. an independent reviewer approves the exact commit; and
6. the approved change is merged before the next dependent gate starts.

Passing a narrow fictional gate does not imply that its production counterpart
is complete.

## Delivery roadmap

### Gate 0 — source preservation and contract reconciliation

**Status:** complete and merged.

Preserve the supplied blueprint and 12-role library byte-for-byte, reconcile
contradictions, define canonical records and checksums, and express executable
acceptance evidence.

**Evidence:** repository source documents, reconciliation, schemas, acceptance
matrix and repository-contract tests.

### Gate 1 — fictional tenant-safe publication path

**Status:** complete and merged.

Prove one fictional article can move through typed stages while public content
remains separate from internal notes; enforce role and tenant boundaries,
immutable exact-version approval, a local mock publication manifest and
receipt, measurement and safe learning filters.

**Evidence:** vertical-slice, store, contract, workflow and gateway tests.

### Gate 2 — shared idempotency and uncertain-result recovery

**Status:** complete and merged.

Make duplicate publication safe across workers and restarts on one computer.
Persist `UNKNOWN` outcomes and require reconciliation instead of blind retry.

**Evidence:** concurrent, process, restart, rebound-key and unknown-outcome
tests against the durable local ledger.

### Gate 3 — durable capability authority

**Status:** complete and merged.

Require an active exact capability at preflight and immediately before
dispatch. Persist grants and suspensions safely for local workers. Refuse
unsafe permissions, symlinks and replaced authority storage.

**Evidence:** capability lifecycle, scope, revalidation, shared-store,
permissions and storage-identity tests.

### Gate 4 — authenticated runtime, credential broker and restricted egress

**Status:** complete and merged.

Observe and catalogue one already-running fictional worker identity per
operating-system process before giving that worker an endpoint. Run the identity
catalogue, signer, gateway, capability authority, credential broker and mock
publisher inside an independently started protected host. Start the worker
without inheriting authority memory and give worker code only an IPC client. On
connection derive peer PID/user, executable checksum and
PID-bound process-start facts; do not accept a caller-supplied identity callback,
principal, runtime ID, observation, time or boundary wiring. Use one-use runtime
assertions with a hard 30-second maximum and reject missing, overlong, expired,
replayed, other-process or changed-runtime assertions. Allow the mock adapter to
obtain only the exact short-lived fictional credential approved for the
authenticated identity, client, destination, environment and operation. Deny
credential scope drift, unapproved destinations, direct adapter access and
non-allowlisted egress.

This gate remains fictional and local: no real passwords and no network calls.

**Exit evidence:** Linux-only repository gate enforced in CI; allowed mock
dispatch plus adversarial missing, overlong, expired, replayed, changed-runtime,
unregistered second-process self-provisioning, base-setter replacement,
wrong-role, cross-brand, credential-scope, destination and bypass tests. Final
credential consumption must also deny approval, schedule or capability expiry
and any suspension that completes after adapter entry but before credential
release. Production remains blocked on an authority service account, separately
provisioned worker processes/UIDs and a persistent identity catalogue for every
real role and tenant.

**Merged evidence:** PR #5, reviewed commit
`478d4acf79831c14a0d2d5b2a9d2ec1b8bc8ba54`, merge commit
`621e54ca54e8b3ad7b753496919a9947e07989a5`, and Ubuntu repository gate
`30303260880` with 77 tests and no real external write.

### Gate 5 — authoritative platform adapters and tenant data foundation

**Status:** candidate complete; retention merged, integration slice pending merge.

The first fictional local slice adds an independently started Platform Authority
host that alone owns Paperclip-shaped SQLite state, the approval signer and
verifier, tenant evidence, and an exact principal-to-client catalogue. Workers
receive only principal-bound IPC clients. The slice also adds versioned brand
approver policy, deadline-enforced and restartable non-authoritative Buzz
context with decision write-back, and tenant-scoped audit events. It is
documented in
`docs/gate-5-platform-foundation.md`. This is reference behavior, not evidence
of authenticated mutation against installed Paperclip or Buzz services.

The second bounded slice records the exact read-only target-host contract:
Paperclip `2026.720.0`, its versioned paths, executable, primary unit and exact
systemd drop-in graph, package/route/reference checksums, exact reviewed route
surface, private authenticated health shape, and the current Buzz binary and
command surface.
A local verifier rechecks those facts and fails closed on drift without credentials,
messages or task mutation. This admits the installed interface contract; it does
not yet prove authenticated task, approval or Buzz lifecycle integration.

The third bounded slice puts immutable artifacts and governed learning in the
same protected authority database. Principal-bound clients provide role/tenant
writes, authority-clock learning reads, authority-attested logical export and
empty same-tenant restore under a pinned recovery identity. Current-export-bound
artifact deletion writes a durable content-free tombstone to a separate protected
authority ledger shared by every recovery host, so a retained signed export
cannot recreate an offboarded tenant in a fresh artifact database. Full Platform
Authority backup, production deletion-ledger replication, retention and
offboarding remain future work.

The fourth bounded slice adds a fictional local durable work queue inside the
protected authority database. Immutable work is bound to one tenant, role and
exact current Paperclip task version. It uses renewable one-use leases, fixed
attempt bounds, durable dead letters and director-owned destination
reconciliation. External unknown or expired-lease work cannot retry blindly, and
queue delivery never mutates authoritative Paperclip task state. Real dispatch,
credentials and multi-host failover remain future work.

The fifth bounded slice adds irreversible, evidence-bound local queue
offboarding. Uncertain external results must be reconciled first. Cancellation
then clears active internal leases, preserves immutable queue history, blocks all
future worker access or queue mutation across restart, and produces a
content-free receipt which artifact deletion must bind. This is not deployed
cross-host cancellation, retention expiry or full tenant-data offboarding.

The sixth bounded slice coordinates destructive offboarding inside the same
fictional local authority. A director prepares a content-free manifest of table
counts and aggregate checksums after queue cancellation. The exact-current
manifest then binds artifact and authority tombstones before task, approval,
Buzz, evidence, artifact, queue and ordinary audit content is removed. Old
clients fail closed immediately; an interrupted cleanup resumes only with the
same manifest and evidence, restart cannot reactivate the tenant, and another
tenant remains isolated. This is not a restorable full-authority export,
production backup/media erasure, credential revocation or multi-host deletion.

The seventh bounded slice adds one complete content-bearing logical export for a
fictional tenant's task, approver-policy, approval, Buzz, evidence, artifact,
queue, queue-cancellation and audit state. The protected authority binds fixed
row shapes and table counts to one checksum and non-public attestation. Restore
is director-only, atomic, same-tenant and empty-target under the same authority
key and protected deletion ledger. Another tenant remains intact, and any
artifact-deletion or full-authority tombstone permanently denies resurrection.
This is not encrypted backup storage, streaming transfer, deployed replication,
key rotation, retention expiry, measured recovery objectives or multi-host
disaster recovery.

The eighth bounded slice adds fictional single-host audit governance without
selecting a production value. A director records an immutable, versioned,
evidence-bound and host-authenticated minimum which can only be strengthened;
a protected append-only anchor rejects rewritten or rolled-back history.
Director/reviewer telemetry exposes tenant-scoped content-free counts and
timestamps. Expiration requires an exact current manifest after the window,
preserves an immutable receipt with only an opaque evidence binding and no actor
ID, survives restart and logical recovery, and retains the expiration audit
event. Offboarding removes policy content but preserves the
receipt. This is not owner approval of the proposed 400-day target, production
monitoring, a deployed scheduler, multi-host expiry, backup expiry or media
erasure.

**Merged first-slice evidence:** PR #6, reviewed commit
`5b53ef937bb0b05490e851660967c5ac39334ac4`, merge commit
`4efc84fc36c4cd14d8226700162a4e8a4fbb3b57`, and Ubuntu repository gate
`30309918344` with 94 tests and no real external write.

**Merged second-slice evidence:** PR #7, reviewed commit
`d8c360c23ea857266ccc8e55b759720294eea95b`, merge commit
`23d8c0ff72bd7c8d46703e0a04978831660512ea`, and post-merge Ubuntu repository
gate `30314681544` with 102 tests and no real external write.

**Merged third-slice evidence:** PR #8, reviewed commit
`388cdee05b522d01515b98cc6864b731ab4fbcef`, merge commit
`edac6d0acaa455a3a39c8130e4c8f99bb14a1afc`, and post-merge Ubuntu repository
gate `30333066091` with 107 tests and no real external write.

**Merged fourth-slice evidence:** PR #9, reviewed commit
`34156a623896f9659f4ee8f46a6f3c1e6972ab1f`, merge commit
`b9b4eb93864f87db6b0218de4c0bf20221601242`, and post-merge Ubuntu repository
gate `30335486258` with 111 tests and no real external write.

**Merged fifth-slice evidence:** PR #10, reviewed commit
`46c4d48a9025e430a122291b8e577000880c7815`, merge commit
`85c7c7f1c9b5e4042e80d4d615d33014bef9a4a3`, and post-merge Ubuntu repository
gate `30339639182` with 112 tests and no real external write.

**Merged sixth-slice evidence:** PR #11, reviewed commit
`8243b5b9f6f39fd7c744d8df34e3e0dc4d26e516`, merge commit
`503930c8e3e95f45455dc4ce4f58e292c7c2c7da`, and post-merge Ubuntu repository
gate `30346643950` with 115 tests and no real external write.

**Merged seventh-slice evidence:** PR #12, reviewed commit
`539fdbea2577f3271942fdc7686310ca4b5ea43f`, merge commit
`830b402195f963341c1dbe1e07cc671abb350d75`, and post-merge Ubuntu repository
gate `30348732163` with 116 tests and no real external write.

**Merged eighth-slice evidence:** PR #13, reviewed commit
`c20e29055095947b08f1c05fe77cc08a66e0ee98`, merge commit
`d6e0b27462125ac7208b6c867e09611afa67b0cc`, and Ubuntu repository gate
`30355458531` with 118 tests and no real external write.

The rebaselined Gate 5 exit is intentionally bounded to:

- exact typed lifecycle adapters for the installed Paperclip issue, approval and
  cost routes;
- a separate board decision transport, UUID-valid native records and an immutable
  one-company-per-brand binding;
- Paperclip-authoritative Buzz decision write-back, a strict command/flag
  allowlist, credential-minimising transports and actionable failures;
- the already-proven single-host tenant, evidence, artifact, learning, queue,
  audit, recovery and offboarding controls; and
- checksum-bound bundle candidates and fresh-process checksum verification for
  all 12 Agency OS roles, without claiming target runtime loading.

Authenticated target-service mutations and Hermes fresh-session loads remain
activation evidence, not claims made by the repository. Production store
deployment, multi-host failover, production retention scheduling, backup
expiry, media erasure and recovery-objective drills move to Gate 12. This
rebaseline is recorded in `docs/delivery-rebaseline.md`.

Host versions, paths and installed services must be recorded from the actual
target VM before activation claims are made.

### Gate 6 — governed product-decision workshop

**Status:** time-boxed owner decision packet; parallel and non-blocking for Gate 7.

Use OpenAI Responses API and Agents SDK for structured, evidence-backed
research behind a self-owned, read-only Agency OS Decision MCP broker.
Paperclip remains the only task, approval and audit authority. LangGraph OSS is
the fallback if self-hosting or model-provider portability becomes mandatory.

The broker exposes only:

- `catalog.search`;
- `policy.evaluate`;
- `cost.estimate`; and
- `evidence.record`.

Four read-only roles — researcher, integration architect, privacy/risk
reviewer and cost challenger — produce a canonical `DecisionPacket`. Every
claim includes primary-source URL, retrieval time, claim or extract, source
class and confidence. Paperclip approval binds the packet hash, tenant, policy
revision and effective date; a catalogue or policy change expires it.

**Fictional proof:** `northstar-bicycles` compares sandbox options for CMS,
analytics, keyword data, social scheduling, CRM, documents, image generation,
approval policy, retention, service targets, budget and client access.

**Denial evidence:** cross-tenant access, hidden credential, missing citation,
stale policy/approval, altered packet, unknown vendor, direct MCP bypass and
purchase/connect attempts all fail with no external write or credential
exposure.

No vendor OAuth, trial, purchase, live client data or direct decision-agent
egress is permitted in this gate. Model traces are diagnostic evidence, not
the audit ledger.

The independent recommendation was checked against primary documentation:

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI Agents SDK MCP controls](https://openai.github.io/openai-agents-js/guides/mcp/)
- [OpenAI Agents SDK approval interruptions](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/)
- [OpenAI API data controls](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint)
- [LangGraph persistence](https://docs.langchain.com/oss/javascript/langgraph/persistence)
- [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

Do not build additional Decision MCP control-plane depth now. Capture only the
owner choices needed before Gate 11 in a checksum-bound Paperclip decision
packet, using cited research when a choice is actually due.

### Gate 7 — complete fictional Search Authority Core slice

**Status:** complete and verified on the live Hermes, Paperclip and Buzz system.

Run one article or landing page through live Paperclip work:

1. brand onboarding and brief intake;
2. sourced research and opportunity-led strategy;
3. drafting and visual requirements;
4. search and answer optimisation;
5. independent QA with at least one reject-and-revise path;
6. exact-version human approval;
7. sandbox publication through the gateway;
8. independent post-publication validation;
9. sourced measurement and an optimisation proposal; and
10. a validated, freshness-limited learning record.

The existing reference flow is test scaffolding, not completion of this gate.

The Lantern candidate now proves all ten stages on the installed Paperclip/Buzz
adapter contracts, including a checksum-bound `REVISE` then `PASS`, exact human
board approval before one protected mock publication, and a receipt that binds
the Paperclip approval ID and evidence checksum. Rejected, pending and altered
approvals all leave the publisher call count at zero. Measurement, optimisation,
learning and closure cover eight role-owned Paperclip tasks through a
transport-opaque engine and campaign-scoped operator projection. Activation
evidence was completed on 2026-07-29 with authenticated Paperclip and Buzz plus
fresh-session loading by every installed Hermes role.

### Gate 8 — optional fictional Social Amplifier

**Status:** complete and verified on the live system.

The branch starts only from an approved, published Core asset. It creates
visual, social, QA, publishing, and measurement tasks, binds the social package
to the canonical checksum, requires exact approval, and performs no action when
the product flag is off.

### Gate 9 — two-brand isolation proof

**Status:** complete and verified on the live system.

Two independent brands complete Core work with separate Paperclip and Buzz
bindings. Cross-brand Social Amplifier startup is denied before task creation.

### Gate 10 — operator and client experience

**Status:** implemented as a complete read-only operator interface.

The loopback-only portal supplies portfolio, brand, campaign, approval,
calendar, performance, and administration views. It reads Paperclip and never
creates a second task authority or mutation path.

### Gate 11 — staged real integrations

**Status:** complete through controlled manual handoffs; typed connections are
activated individually when the owner supplies a real account and credential.

CMS, analytics, Search Console, keyword data, social, creative, and CRM service
classes all have explicit provider-neutral handoffs. A service is never marked
connected until a typed adapter, scoped credential, destination, and acceptance
check exist. Real client publication still requires exact owner approval.

### Gate 12 — production operations and final acceptance

**Status:** complete. The owner approved production activation and the installed
system passed its full live production verifier on 2026-07-29.

Complete observability, service objectives, budgets, incident handling,
security review, retention and offboarding, full acceptance evidence, rollback
instructions, independent approval, and owner activation. VM backup and disaster
recovery are managed by the human VM owner and are not Agency OS completion
requirements.

## Product decisions reserved for the owner

The system can progress through fictional gates before these are required.
Before Gate 11, the owner must decide:

- supported CMS, analytics, search/keyword, social/scheduling, CRM, document
  and image services;
- approval roles, delegation and whether approved publication is automatic or
  manually started;
- retention, export and deletion policy;
- service and incident-response targets;
- per-brand and per-campaign budgets;
- client access and visibility; and
- dedicated-VM promotion thresholds.

Technical gate decisions are made through independent implementation and
assurance review. Product choices remain owner decisions supported by the
governed DecisionPacket workshop.

## Current checkpoint

- Status: live production. Core and Social workflows, two-brand isolation,
  12 checksum-bound Hermes role bundles, authenticated Paperclip and Buzz,
  provider handoffs, and the read-only operator portal are active and verified.
- Evidence: 144 repository tests passed; all 12 roles passed fresh-session and
  role-boundary checks; the live Core, Social and isolation workflows passed;
  the secret audit passed; and the VM reported zero failed systemd units.
- Real provider accounts remain manual handoffs until the owner supplies each
  account, scoped credential and destination.
- VM backup and disaster recovery remain outside this plan.
