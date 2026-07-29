# Fleet G2.0 and G2.1 completion evidence

Status: pass

This record covers only Gates G2.0 and G2.1. It does not claim that the Living
Brand Twin, AI Market Observatory, Brand Agent, external client portal or
agentic commerce are complete.

## Plain-English result

Fleet now has the safe base for one modular product.

- Fleet DMA is the one active internal Paperclip company.
- `brand_fleet` is permanently linked to Fleet DMA's company UUID.
- the working Content Engine is enabled for Fleet DMA;
- every new Generation 2 module is disabled until a later gate explicitly
  enables it;
- `fleet.madebyfleet.com` is reserved in the protected tenant registry for the
  future portal, but no public DNS or portal availability is claimed;
- Brand Twin and Observatory data shapes are defined without inventing any
  brand facts or observation results; and
- the complete existing Agency OS release suite still passes.

## Production baseline

| Check | Observed result |
|---|---|
| Baseline Agency OS commit before G2 work | `51b059d6ed3cd5e6607c55cfe543ffdb263b167f` |
| Baseline `origin/main` | same commit; no behind/ahead drift |
| Paperclip package | `2026.720.0` |
| Hermes package | `0.19.0`, commit `7de554277de632364c74fcf8641daa58a9a977d9` |
| Paperclip service | active |
| Agency OS operator service | active |
| Tailscale proxy service | active |
| Paperclip network policy | active |
| Active Paperclip company | Fleet DMA |
| Fleet DMA company UUID | `d7e2e389-c7ad-486e-87ca-482e4ec6216d` |
| Other company state | Agency OS Isolation Acceptance is archived, not active |
| Agency OS core roles | 12 present; all 12 idle and available on demand |

The Paperclip company name file installed during the original appliance build
still contains its historical display name. It is not authoritative. The live
Paperclip API name and immutable company UUID are authoritative; the UUID still
matches the pinned `/etc/paperclip/company-id` value.

## Live Fleet binding

The idempotent initializer wrote the following protected authority state:

| Field | Value |
|---|---|
| Database | `/var/lib/agency-os/fleet-tenancy.sqlite3` |
| Directory owner and mode | `root:root`, `0700` |
| Database owner and mode | `root:root`, `0600` |
| Authority schema | `1` |
| Tenant | `tenant_fleet` |
| Brand | `brand_fleet` |
| Paperclip company | Fleet DMA |
| Paperclip company UUID | `d7e2e389-c7ad-486e-87ca-482e4ec6216d` |
| Approved future hostname | `fleet.madebyfleet.com` |
| Enabled module | `content_engine` |
| Disabled modules | Brand Twin, Observatory, Brand Agent, controlled actions, client portal, measurement and agentic commerce |

The initializer was run twice against protected temporary storage and produced
the same result both times. It was then run against the live company-ID pin
before production state was created.

## Paperclip programme evidence

Paperclip company: Fleet DMA

Goal:

- ID: `446dfd0c-db89-4b90-9b02-9f78bc0e5762`
- title: Fleet Generation 2 — unified brand operating platform

Project:

- ID: `08bd87ef-1f2d-4d83-bd33-d784d0dad003`
- name: Fleet Generation 2

Gate and workstream issues:

| Issue | Work |
|---|---|
| `PAP-152` | FL2-00 — Generation 2 programme control |
| `PAP-153` | FL2-10 — tenant, hostname and product entitlements |
| `PAP-154` | FL2-20 — Brand Twin and Observatory foundation contracts |
| `PAP-155` | FL2-30 — Fleet Living Brand Twin |
| `PAP-156` | FL2-40 — customer mission registry |
| `PAP-157` | FL2-50 — AI Market Observatory |
| `PAP-158` | FL2-60 — closed-loop Content Engine proof |
| `PAP-159` | FL2-70 — governed Fleet Brand Agent |
| `PAP-160` | FL2-80 — private multi-tenant client portal |
| `PAP-161` | FL2-90 — first external client pilot |
| `PAP-162` | FL2-100 — scale and agentic commerce decision |

The issues are children of the programme-control issue. Paperclip blocker
relations encode the dependency graph. Representative API checks confirmed:

- `PAP-154` is blocked by `PAP-153`;
- `PAP-158` is blocked by the Fleet Brand Twin and Observatory; and
- `PAP-161` is blocked by the closed-loop proof, Brand Agent and portal.

Later work remains in backlog. Creating the programme does not activate or
claim completion of those later products.

## Implemented controls

- checksummed immutable tenant, hostname and entitlement records;
- one-to-one brand, tenant and Paperclip company UUID constraints;
- Agency Director-only writes inside the same brand;
- exact server-side hostname resolution with no browser brand override;
- independently enabled product modules, disabled by default;
- tenant-wide and module-specific suspension;
- owner-only durable SQLite with identity pinning, WAL and full sync;
- schema metadata and future-version refusal;
- tenant-scoped durable audit;
- a server-built portal routing projection; and
- JSON contracts for sources, entities, claims, evidence, policies,
  capabilities, missions, observations, findings, remediations, experiments
  and outcomes.

## Verification evidence

Release command:

```bash
./scripts/verify
```

Result: 161 tests passed, followed by successful fictional publication, full
Core workflow demonstration and checksum verification of all 12 runtime role
bundles.

The 161 tests include 16 new G2 tenant-authority tests plus every existing
Content Engine, approval, capability, gateway, Paperclip, Buzz, storage,
operator, provider-handoff and workflow test. The complete Core content flow
still includes a real revision, exact approval and authoritative closure.

Focused initializer check:

```bash
python3 scripts/initialize_fleet_tenant.py --database PROTECTED_TEMP_PATH
python3 scripts/initialize_fleet_tenant.py --database PROTECTED_TEMP_PATH
```

Both runs returned the same tenant, hostname and module state.

## Gate conclusion

G2.0 passes because the live baseline is pinned, Fleet DMA is confirmed as the
internal tenant, architecture decisions and acceptance evidence are recorded,
the Paperclip programme is visible, all 12 roles remain available, and the
working Content Engine is unchanged.

G2.1 passes because tenant, hostname and entitlement authority is durable and
fail-closed; the foundational Generation 2 contracts exist; module activation
is independent; cross-tenant access looks absent; migrations, audit, restart
and storage protections are tested; and the full existing release suite passes.

External backup mounts and key escrow are not completion requirements. They
remain a top-level VM administration choice, as directed by the human owner.
