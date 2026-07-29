# Fleet G2.0 and G2.1 correction-release evidence

This record covers only Gates G2.0 and G2.1. The authoritative current gate
state is in Paperclip issues `PAP-152`, `PAP-153` and `PAP-154`. A static file
must never override those issue states or the latest durable appliance result at
`/var/lib/paperclip-appliance/verification-result.json`.

It does not claim that the Living Brand Twin, AI Market Observatory, Brand
Agent, client portal, external client pilot or agentic commerce are complete.

## Why a correction release was required

Independent review found that the first G2 foundation pass overstated assurance.
The JSON Schema had no root record constraint, runtime records accepted unknown
fields, initialization could leave partial state, denied mutations were not
consistently audited, entitlements had no replacement lifecycle, the portal read
model did not require its own entitlement, and the database had no demonstrated
migration. The appliance also allowed Tailscale's UDP/41641 rule to run before
the Paperclip container-source guard, and its production verifier did not inspect
the live G2 database or retain its six-line result.

The three completed Paperclip gates were therefore reopened before correction
work began. Their earlier closure is not valid evidence for this release.

## Corrected source controls

- The G2 schema root accepts only one of the 16 defined business records.
- Every defined record is a closed object and uses the strict normalised
  `brand_id` contract.
- Positive and negative Draft 2020-12 fixtures cover every record type.
- The runtime rejects unknown tenant, hostname and entitlement fields.
- Tenant, hostname and initial entitlements commit as one transaction.
- Role, actor, contract, tenant, not-found and write denials produce durable,
  brand-scoped audit events.
- Entitlements are append-only versions with contiguous numbers, explicit
  supersession, effective and expiry times, and independently suspended state.
- The client portal projection requires the `client_portal` entitlement.
- Database schema 1 is transactionally migrated to schema 2; future versions
  still fail closed and the resulting table shape, primary key, foreign keys,
  WAL mode and quick-check result are verified.
- Production initialization requires the exact `/etc/paperclip/company-id` pin.
- A read-only live verifier checks the protected database without adding audit
  events.
- A reusable, versioned, dependency-only Paperclip programme template is
  checked in. It contains no weekly, sprint or calendar tracking.

## Fleet binding that must be observed live

| Field | Required value |
|---|---|
| Paperclip company | `Fleet DMA`, active and not archived |
| Paperclip company UUID | `d7e2e389-c7ad-486e-87ca-482e4ec6216d` |
| Tenant | `tenant_fleet` |
| Brand | `brand_fleet` |
| Authority database | `/var/lib/agency-os/fleet-tenancy.sqlite3` |
| Authority schema | `2` |
| Directory owner and mode | `root:root`, `0700` |
| Database owner and mode | `root:root`, `0600` |
| Reserved hostname | `fleet.madebyfleet.com` |
| Enabled module | `content_engine` only |
| Disabled modules | Brand Twin, Observatory, Brand Agent, controlled actions, client portal, measurement and agentic commerce |

## Appliance corrections

The Paperclip container-source INPUT hook must be first, including after every
Tailscale restart. The verifier sends a container UDP/41641 probe and proves it
never reaches Tailscale's chain. The appliance then runs an authenticated
Paperclip company check and the read-only live G2 database verifier before it
may print `AGENCY OS: LIVE`.

Every full `sudo ./verify.sh` run writes its exact six gate lines, real exit
status, boot ID, Agency OS commit, verifier checksum, appliance-lock checksum,
installed-assets checksum and G2 summary to the protected durable result file.
A failed run is recorded as failed and cannot inherit a previous pass.

## Repository verification

The correction candidate ran:

```bash
./scripts/verify
```

Result: 174 tests passed, followed by the successful fictional publication,
complete Core workflow demonstration and checksum verification of all 12
runtime role bundles. This includes strict-schema positives and negatives,
atomic rollback, production pinning, entitlement replacement, denial audit,
portal entitlement, real v1-to-v2 migration and read-only live-verifier tests.

## Closure rule

G2.0 and G2.1 may return to `done` only after all of the following are true:

1. the correction pull requests have passed their independent repository checks
   and been merged;
2. immutable Agency OS and appliance commits are pinned and installed;
3. the live Tailscale/Paperclip boundary, G2 database and authenticated Fleet DMA
   binding pass;
4. `sudo ./verify.sh` prints all six required lines with exit status zero;
5. the durable result is checksum-bound to that verifier and release; and
6. exact merge, deployment and evidence identifiers are attached to
   `PAP-152`, `PAP-153` and `PAP-154` before they are closed.

External backup mounts and key escrow remain outside these completion checks,
as directed by the human VM owner.
