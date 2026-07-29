# Fleet Generation 2 foundation decisions

Status: accepted for Gates G2.0 and G2.1

These decisions turn the enterprise plan into enforceable boundaries. They do
not build the Living Brand Twin, Observatory, Brand Agent or client portal.
They make those products safe to add without weakening the working Content
Engine.

## Decision 1 — one Fleet product, separate client tenants

Fleet will sell one unified product. Automated content production remains a
first-class module. The Brand Twin, AI Market Observatory, Brand Agent,
controlled actions, measurement and portal are additional modules in that same
product.

Fleet DMA is Fleet's internal Paperclip company and first internal tenant. Each
external client will receive a separate Paperclip company and a separate
`brand_id`. A second company is not used merely to represent another module or
a test environment.

Why:

- clients get one coherent service instead of two disconnected products;
- content can use approved Brand Twin facts and Observatory findings later;
- module access can still be sold and enabled independently; and
- Paperclip's company boundary remains the strongest client-data boundary.

## Decision 2 — authorities have narrow, non-overlapping jobs

Paperclip remains authoritative for goals, projects, tasks, dependencies,
assignments, approvals, budgets and the human-visible activity trail. Buzz may
help agents communicate but never becomes task or approval authority.

The Generation 2 tenant authority owns only:

1. the immutable link between a `brand_id` and one Paperclip company;
2. approved `madebyfleet.com` hostnames for that brand; and
3. the product modules enabled for that brand.

Existing Agency OS content stores, approval controls, capability controls and
publication gateways keep their current jobs. The tenant authority does not
copy or replace them.

Why: one clear owner for each decision is easier to audit and much harder to
bypass.

## Decision 3 — tenant binding is immutable

The authoritative key is `brand_id`. Each active brand has exactly one
`tenant_id` and one Paperclip company UUID. Company names are display labels;
they can change in Paperclip without changing the UUID.

Registering the exact same checksummed binding again is safe. Reusing a brand,
tenant or Paperclip company UUID for a different binding is denied. A binding
is suspended rather than edited in place.

The internal binding is:

| Field | Value |
|---|---|
| Business | Fleet |
| Paperclip company | Fleet DMA |
| Paperclip company UUID | `d7e2e389-c7ad-486e-87ca-482e4ec6216d` |
| Tenant | `tenant_fleet` |
| Brand | `brand_fleet` |

The archived isolation-acceptance company remains historical evidence. It is
not active and is not a client tenant.

## Decision 4 — modules are explicit and off by default

The module catalogue is:

- `content_engine`
- `brand_twin`
- `ai_market_observatory`
- `brand_agent`
- `controlled_actions`
- `client_portal`
- `measurement`
- `agentic_commerce`

An absent, unknown or suspended entitlement is disabled. Suspending a tenant
disables all of its modules. One module can be suspended without affecting
another.

Fleet DMA starts with only `content_engine` enabled. This preserves current
production behaviour. Later gates must explicitly enable their own modules
after their acceptance evidence passes.

## Decision 5 — portal hostnames are resolved on the server

The future portal will accept only an exact hostname recorded in the protected
registry. The server resolves that hostname to a brand and then compares the
result with the authenticated user's brand. A browser request cannot choose or
override `brand_id`.

Fleet's reserved future hostname is `fleet.madebyfleet.com`. The binding does
not publish DNS, expose Paperclip, or claim that the G2.6 portal exists.

Infrastructure labels including `admin`, `api`, `app`, `auth`, `paperclip` and
`www` cannot be used as client brand slugs. Unknown, suspended, cross-brand,
ported or URL-shaped host values fail as not found.

## Decision 6 — Generation 2 records are evidence-first

The foundation schema defines:

- brand sources, entities, claims, claim evidence, policies and capabilities;
- customer missions, observation runs and individual observations;
- market findings and remediation proposals; and
- experiments and outcome events.

Every record is brand-scoped and checksummed. Claims point to versioned source
evidence. Findings keep facts, inferences and unknowns separate. Outcome events
state whether a result was merely observed, correlated, experimentally
supported or still unknown.

These contracts do not assert that any Fleet Brand Twin or Observatory data
already exists. G2.2 and G2.3 will create real records only from approved
sources and permitted observations.

## Decision 7 — storage is protected and restart-safe

The authority uses SQLite at
`/var/lib/agency-os/fleet-tenancy.sqlite3` in production.

- parent directory: root-owned mode `0700`;
- database: root-owned mode `0600`;
- SQLite file and parent identities are pinned while the authority is open;
- WAL and full synchronous durability are required;
- foreign keys are enabled;
- a schema metadata table controls migrations; and
- a runtime older than the stored schema refuses to start.

Immutable records and operational state are stored separately. Audit entries
record actor, role, brand, operation, target, outcome and timestamp. Audit reads
are brand-scoped.

## Decision 8 — the current Content Engine is not rewritten

G2.0 and G2.1 add a side-by-side foundation. They do not change the current
content workflow, its eight-role Core path, optional Social branch, approval
binding, publication gateway, Paperclip adapter or Buzz boundary.

The complete existing release suite remains blocking. A green G2 test alone is
not enough; the complete Core workflow and all existing security tests must
also pass.

## Decision 9 — delivery uses evidence, not calendar reporting

There are no weekly milestones, sprint promises, monthly delivery quotas or
date-based progress claims. Paperclip blockers express the real dependency
graph. A gate closes only when its exit evidence passes.

Operational timestamps, freshness windows, latency measurements and evidence
capture times remain valid technical data. They are not project-management
deadlines.

## Decision 10 — external backup and recovery are outside this gate

The human VM administrator owns top-level backup and recovery choices. Agency
OS does not make external backup mounts or key escrow a G2.0/G2.1 completion
condition. This does not weaken tenant isolation, record integrity, approval or
runtime fail-closed controls.

## Decision 11 — correction assurance is part of the foundation

The G2 foundation uses a strict root JSON Schema and closed objects, and tests
positive and negative instances for every record type. Runtime validation must
match the schema's unknown-field rejection. Initial setup is atomic. Every
denied authority mutation is audited. Entitlements are append-only versions
with explicit supersession and effective windows. The portal read model requires
its own entitlement.

A database version change must include a real migration test, not only refusal
of a future version. Production setup requires the exact Paperclip company UUID
pin. Gate closure requires reviewed merge provenance and live, durable,
checksum-bound appliance evidence; a direct commit or a static completion note
is insufficient.

## Failure behaviour

The authority fails closed when:

- a brand, tenant, company UUID, hostname or entitlement conflicts;
- a checksum is invalid;
- a caller uses the wrong role or brand;
- a hostname is unknown or belongs to another brand;
- an entitlement is missing, unknown or suspended;
- the tenant is suspended;
- protected storage ownership, mode or file identity changes; or
- the stored database schema is newer than the running code.

No failure falls back to a client-supplied brand or an automatically enabled
module.

## Implementation map

| Concern | Implementation |
|---|---|
| Durable authority | `agency_os/fleet_tenancy.py` |
| Foundational contracts | `schemas/fleet-generation2.schema.json` |
| Fleet DMA binding | `config/fleet-generation2.json` |
| Idempotent initializer | `scripts/initialize_fleet_tenant.py` |
| Release tests | `tests/test_fleet_tenancy.py` |
| Acceptance map | `acceptance/fleet-generation2-foundation.json` |
| Gate evidence | `docs/fleet-g2-foundation-evidence.md` |

## Superseding these decisions

A later change must be recorded in Paperclip, explain the security and product
effect, remain backward-compatible or include a tested migration, and pass the
same isolation and Content Engine regression gates. A code change alone does
not silently replace an accepted decision.
