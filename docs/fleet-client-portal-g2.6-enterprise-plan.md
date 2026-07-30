# Fleet client portal G2.6 enterprise plan

- **Status:** implementation complete; live external commissioning pending
- **Business:** Fleet
- **Internal pilot:** Fleet DMA
- **Public experience:** `fleet.madebyfleet.com`
- **Fleet administration:** `admin.madebyfleet.com`
- **Identity broker:** `auth.madebyfleet.com`
- **Delivery rule:** dependency-and-evidence only; no calendar milestones

## 1. Executive decision

G2.6 creates the first proper Fleet product experience. It is not a Paperclip
skin and it does not expose Paperclip to clients. It is a premium Fleet
experience over the existing Agency OS authorities, with Paperclip continuing
to own operational work, dependencies and approvals.

The approved account model is:

```text
Customer account
  -> Client brand
    -> Operational tenant
      -> Paperclip company
```

One client brand has one active operational tenant for G2.6. The model keeps
the operational tenant as a separate, immutable object so a future legal or
operational requirement can divide a brand into separately isolated tenants
without changing the meaning of existing records.

G2.6 production admits Fleet DMA only. Two disposable test tenants prove
isolation. Creating the first external client's real Paperclip company remains
G2.7.

## 2. Product principles

1. Fleet concepts are client-facing; Paperclip concepts stay private.
2. The Launch Room becomes the portal instead of handing a client between
   disconnected forms and products.
3. Information is requested progressively and only when its purpose is clear.
4. Every important statement is supported by an admitted source, a named
   owner, a confidence state and an approval state.
5. Every client-visible mutation is a durable, idempotent command.
6. The portal is a projection, never an authority.
7. Tenant identity is derived on the server from the exact hostname,
   authenticated organisation and Fleet membership. The browser cannot choose
   a tenant or brand.
8. Commercial packaging and technical access are related but separate.
9. The portal fails closed when identity, tenancy, entitlement, evidence or
   downstream outcome is uncertain.
10. G2.6 preserves the working Content Engine, Brand Twin, Observatory and
    Brand Agent.

## 3. Intended user journey

### 3.1 Discovery case

Fleet creates a minimal Discovery Case for a prospective client. It holds only
the information needed to understand the opportunity, prepare a proposal and
identify who may authorise access. It is not a production tenant and cannot
run agents or submit work to Paperclip.

### 3.2 Secure Launch Room

After authority is confirmed, Fleet invites the client's authorised users into
a secure Launch Room. The room:

- explains what Fleet needs and why;
- accepts approved files and carefully bounded website sources;
- shows extraction and review status;
- presents candidate facts and claims for confirmation;
- records decisions and unresolved questions; and
- becomes the client's permanent Fleet portal when activation passes.

### 3.3 Operational activation

Fleet creates or admits the operational tenant, binds its Paperclip company,
reserves its hostname, configures identity and access, grants purchased
entitlements, installs programme templates, runs isolation and assurance
checks, and only then changes the tenant to `active`.

### 3.4 Ongoing operation

Clients use the portal to understand current activity, supply evidence, make
decisions, inspect their Brand Twin, review content, monitor AI presence and
manage their own authorised users. Fleet uses the administration experience
to manage the account, tenant lifecycle, entitlements, support and assurance.

## 4. Information model

### 4.1 Customer account

The commercial and identity parent for one customer relationship. It owns:

- legal and trading names;
- billing and commercial contacts;
- identity-provider organisation;
- account-level Fleet ownership;
- order and subscription references; and
- one or more client brands.

### 4.2 Client brand

The durable product concept representing one brand. It owns:

- brand name, domain and reviewed slug;
- business and legal context;
- authorised client owners;
- source register and Brand Twin relationship;
- commercial package selection; and
- one current active operational tenant.

### 4.3 Operational tenant

The hard operational isolation boundary. It owns:

- immutable `tenant_id`;
- immutable operational `brand_id`;
- one immutable Paperclip company binding;
- exact approved hostname;
- lifecycle and assurance state;
- memberships and product entitlements;
- data, commands, audit and support scope.

A future split creates new operational tenants, new brand IDs, new Paperclip
companies and separately isolated data. Existing IDs are never rebound.

## 5. Experience architecture

### 5.1 Client portal

`fleet.madebyfleet.com` is the Fleet DMA portal in G2.6. The reusable client
information architecture is:

- `/` — home, progress, decisions and current value;
- `/launch` — source collection, setup and activation;
- `/decisions` — approvals and questions requiring attention;
- `/brand` — approved Brand Twin facts, claims and evidence;
- `/content` — durable content catalogue and workflow status;
- `/ai-presence` — Observatory missions, findings and changes;
- `/settings` — people, access, notifications and plan summary.

### 5.2 Fleet administration

`admin.madebyfleet.com` is a separate Fleet-only surface:

- `/accounts`;
- `/brands`;
- `/tenants`;
- `/provisioning`;
- `/users`;
- `/entitlements`;
- `/support`;
- `/health`; and
- `/audit`.

Client and administration navigation, permissions and projections are
structurally separate. A client role cannot become a Fleet role through URL or
request changes.

### 5.3 Visual direction

The product uses a premium hybrid direction: calm editorial hierarchy for
client understanding, precise enterprise controls for decisions, and dense
operational views only where Fleet operators need them. Accessibility is a
release requirement, not a later styling pass.

## 6. System authorities

| Concern | Authoritative system |
|---|---|
| customer, brand, tenant, hostname and lifecycle | Fleet control plane |
| login identity and customer organisation | WorkOS AuthKit |
| tenant memberships and approval scopes | Fleet authorisation authority |
| goals, projects, tasks, dependencies and approvals | Paperclip |
| informal agent collaboration | Buzz |
| facts, claims and admitted evidence | Living Brand Twin |
| content artifacts and lifecycle | Content Engine |
| missions, observations and findings | AI Market Observatory |
| catalogue, quote, order and subscription reference | Fleet commercial authority |
| module and capacity grants | Fleet entitlement ledger |
| browser mutation delivery and reconciliation | Fleet command journal |
| portal screens | replaceable, non-authoritative projection |
| security and business event history | append-only Fleet audit authority |

No portal table is allowed to become a second approval, task, Brand Twin or
Paperclip authority.

## 7. Runtime architecture

### 7.1 `fleet-portal-web`

A Next.js 16 App Router application using React 19.2 and strict TypeScript. It
runs as an unprivileged service and:

- verifies the exact host and request origin;
- validates the Cloudflare Access and WorkOS session;
- builds a server-only request context;
- renders read projections;
- submits idempotent commands; and
- holds no Paperclip credential and no authority database access.

### 7.2 `agency-os-authority`

A hardened local Python authority service reached through a Unix domain
socket. It verifies the calling process identity and owns the protected SQLite
authorities for control-plane, membership, lifecycle, commands, source
admission, catalogue and audit state.

### 7.3 `fleet-command-worker`

The only G2.6 process holding the restricted Paperclip portal credential. It
claims durable commands, uses exact preconditions and idempotency keys,
reconciles uncertain outcomes, and records the authority result before a
projection is refreshed.

### 7.4 `fleet-ingest-worker`

A no-network process that scans and extracts uploaded material, creates
reviewable source and fact candidates, and cannot approve or admit its own
output.

## 8. Identity, sessions and permissions

WorkOS AuthKit supplies login and organisation identity. One WorkOS
organisation maps to one Customer Account. Fleet owns the tenant membership,
role and approval-scope decision.

Client roles:

- Owner;
- Approver;
- Contributor;
- Analyst; and
- Viewer.

Fleet roles:

- Platform Administrator;
- Account Director;
- Operator;
- Assurance Reviewer; and
- Support.

Approval scopes are explicit and narrower than roles. Examples include
`brand_fact`, `claim`, `content`, `publication`, `access_change` and
`commercial_change`.

Session rules:

- invitation validity: 72 hours;
- idle expiry: 60 minutes;
- absolute expiry: 12 hours;
- sensitive-action step-up age: less than 10 minutes;
- membership or session revocation visible within 60 seconds;
- host-only, secure, HTTP-only cookies;
- no bearer token in browser storage.

Every request builds a server-derived `PortalRequestContext` containing the
host, WorkOS subject, organisation, account, client brand, tenant, operational
brand, roles, scopes, session, entitlement version and correlation ID. None of
these security fields is accepted from form data or query parameters.

## 9. Commercial model

The commercial chain is:

```text
catalogue -> package -> quote -> order -> subscription
          -> entitlement intent -> technical entitlement -> capacity
```

G2.6 defines four packages without connecting public checkout:

- Fleet Content Engine;
- Fleet AI Brand Readiness;
- Fleet Content Intelligence; and
- Fleet Brand OS.

Fleet DMA receives an internal, zero-value order so it follows the same
commercial-to-technical path. Prices, Stripe, automated invoicing and public
purchase remain deferred. The client portal may display the active plan and
submit a plan-change request; it cannot silently change entitlements.

## 10. Launch Room data and consent

Every requested item records:

- purpose;
- source;
- owner;
- consent basis;
- visibility;
- sensitivity;
- confidence;
- review state; and
- the product gate it supports.

Collection is progressive. Fleet first asks for the smallest set needed to
understand the brand, then asks for evidence to resolve specific gaps.

Supported launch files:

- PDF;
- DOCX;
- XLSX;
- CSV;
- TXT;
- PNG; and
- JPEG.

Limits:

- 50 MiB per file;
- 250 MiB per Launch Room;
- archives, executables and macro-enabled files rejected;
- extension and magic bytes must agree;
- malware scan required;
- extraction runs without network access;
- OCR uses a local tool;
- extracted statements remain candidates until an authorised person approves
  them.

Website-source admission accepts only HTTPS on port 443. Every redirect and
resolved address is revalidated. Private, loopback, link-local, multicast,
reserved and metadata addresses are denied for IPv4 and IPv6. The fetcher
allows at most three redirects, 10 MiB and 15 seconds, with no JavaScript,
cookies, credentials or browser automation.

## 11. Durable commands and consistency

Every client-visible mutation creates a `PortalCommand` with:

- command ID and idempotency key;
- account, brand and tenant scope;
- authenticated actor and session;
- command type and exact target;
- expected version or checksum;
- approval scope and step-up proof where required;
- canonical payload checksum;
- correlation ID;
- current state; and
- authority receipt or reconciliation evidence.

State machine:

```text
received
  -> dispatching
  -> authority_recorded
  -> projecting
  -> completed
```

Terminal or intervention states are `rejected`, `conflict`, `unknown` and
`cancelled`.

The browser receives an acknowledgement within two seconds. A timeout after
dispatch becomes `unknown`; it is never reported as success. Retries use the
same idempotency key. Stale checksums become `conflict` and require a fresh
decision.

## 12. Complete G2.6 approval journey

The release proves this exact path:

1. Fleet sends an invitation.
2. The Fleet owner authenticates through WorkOS.
3. The user enters the correct host and tenant context is derived server-side.
4. The user supplies a permitted source.
5. The source is quarantined, scanned, extracted and marked for review.
6. The system creates a candidate fact with source location and checksum.
7. The client confirms or corrects the candidate.
8. Fleet reviews the candidate and requests a Paperclip decision packet.
9. The client approves through the portal.
10. The command worker records the decision in Paperclip.
11. The Brand Twin creates a new immutable approved version.
12. The portal projection and append-only audit show the completed result.

Negative acceptance paths include wrong host, wrong organisation, revoked
membership, missing entitlement, disallowed file, unsafe URL, malware result,
stale checksum, duplicate submission, rejected decision, Paperclip timeout,
projection delay and cross-tenant access.

## 13. Lifecycle and provisioning

Lifecycle states:

```text
provisioning -> launch_ready -> assurance -> active
                                       \-> failed_pre_activation
active -> suspended -> active
active -> offboarding -> offboarded
```

Activation requires all of these independently durable steps:

1. Customer Account created or admitted.
2. Client Brand created.
3. Immutable tenant and operational brand IDs issued.
4. Paperclip company created or exact existing company admitted.
5. Company binding verified.
6. Hostname reserved.
7. WorkOS organisation bound.
8. Fleet ownership assigned.
9. Client memberships and scopes configured.
10. Commercial order admitted.
11. Entitlement intent translated into technical grants.
12. Paperclip templates installed.
13. Brand source register opened.
14. isolation, identity and negative-path tests passed.
15. Assurance Reviewer approves activation.

Activation is a distributed workflow, not a pretend atomic transaction.
Incomplete steps are visible, retryable and compensatable. `active` is
impossible until every prerequisite is verified.

G2.6 production may only admit the existing Fleet DMA company. The test adapter
creates two disposable tenants with deliberately identical resource labels to
prove all security decisions use immutable tenant scope, not display names.

## 14. Data evolution and retention

The current tenancy authority migrates from schema v2 to v3 additively. Existing
checksummed v2 JSON records are retained byte-for-byte. New tables add:

- customer accounts;
- client brands;
- operational tenant lifecycle;
- hostname reservation, retirement and tombstones;
- identity organisations;
- provisioning runs and steps;
- catalogue, quote, order and subscription references;
- memberships and sessions;
- discovery and Launch Room records;
- source admission and candidate review;
- portal commands and support sessions;
- durable content catalogue; and
- append-only audit events.

Existing Brand Twin v2 records are not rewritten. The immutable
`brand_id -> tenant_id` binding derives tenant scope for them.

Default G2.6 retention:

- audit and decision evidence: 400 days;
- content-free operational telemetry: 30 days;
- rejected upload quarantine: 7 days;
- admitted sources and derived evidence: until authorised removal or
  offboarding policy applies.

Application export and integrity remain in scope. VM backup and disaster
recovery remain under the human VM administrator and do not block this gate.

## 15. Browser and edge controls

- Cloudflare Access protects the exact portal and admin hosts.
- A Cloudflare Tunnel reaches loopback services; Paperclip remains private.
- Access JWT and WorkOS session are both required in production.
- Host and Origin are exact allowlists.
- Mutations use CSRF protection and same-site cookies.
- Content Security Policy disallows unsafe script execution and framing.
- Sensitive responses are `no-store`.
- Rate limits apply per session, actor, tenant and command type.
- Upload and URL admission have separate resource limits.
- A mutation kill switch disables writes without removing safe read access.
- Admin and client surfaces have independent policy and service identity.

## 16. Acceptance objectives

### G2.6A — product, UX and security contract

- this plan is saved and cross-referenced;
- system-of-record and permission matrices are explicit;
- threat model covers identity, host routing, uploads, SSRF, commands,
  Paperclip projection, support and offboarding;
- responsive client and admin prototypes are accepted by the Fleet owner;
- Assurance approves the gate contract.

### G2.6B — identity, tenancy and provisioning

- v2-to-v3 migration preserves existing records and checksums;
- WorkOS and mock identity adapters share one contract;
- Fleet DMA follows the admission workflow;
- two disposable tenants prove isolation across every store and projection;
- all lifecycle and compensation paths are tested;
- no client DTO exposes a Paperclip company UUID.

### G2.6C — Launch Room and decisions

- source admission controls pass positive and negative tests;
- one fact-candidate journey completes end-to-end;
- Paperclip remains decision authority;
- command replay, conflict and unknown-outcome handling pass;
- Brand Twin version and audit receipt bind to exact checksums.

### G2.6D — portal product and operations

- all client and Fleet administration pages exist;
- the Content Engine has a durable catalogue;
- one controlled Fleet content item is materialised without fabricating
  historical work;
- keyboard, focus, contrast, reduced-motion and screen-reader checks pass;
- responsive browser tests pass;
- full Agency OS and appliance verification pass;
- production enables G2.6 only for Fleet DMA.

## 17. Service objectives

- ordinary portal reads: p95 under 750 ms on the live VM;
- command acknowledgement: under 2 seconds;
- session or membership revocation: effective within 60 seconds;
- cross-tenant access: zero tolerated;
- unknown downstream outcome: never displayed as success;
- accessibility: WCAG 2.2 AA for the implemented experience.

## 18. Rollback

Rollback is additive and evidence-preserving:

1. enable the mutation kill switch;
2. suspend the Fleet DMA `client_portal` entitlement;
3. disable the Cloudflare portal route;
4. stop portal and worker services;
5. retain authority records, audit, checksums and existing G2.5 services.

Rollback never deletes authority history or rebinds a tenant.

## 19. Explicit G2.6 deferrals

- first external client and real external Paperclip company creation;
- public signup and self-service onboarding;
- public checkout, prices, Stripe and automated billing;
- customer SSO/SCIM rollout beyond the adapter boundary;
- custom or client-owned domains;
- cross-brand portfolio views;
- public APIs;
- authenticated source connectors and browser automation;
- Fleet Guide conversational assistant;
- white labelling;
- provider publication, commerce and payment integrations;
- automatic advertising-budget control;
- top-level VM backup and key escrow.

## 20. Implementation record

The bounded G2.6 product and appliance implementation is complete. The release
candidate passes the full Agency OS regression suite (236 tests), frontend
lint and type checks, the production frontend build, and responsive browser
coverage across client and administration routes.

The implementation includes live authority-backed portal projections, secure
source admission, candidate review, Paperclip approval handoff and
reconciliation, immutable Brand Twin materialisation, owner-managed invitations
and revocation, separate client/admin Cloudflare Access audiences, separate
service identities and credentials, and fail-closed appliance commissioning.

Live external commissioning is deliberately recorded as pending until real
WorkOS and Cloudflare values are installed and the appliance verifies the exact
organisational membership, Access applications and policies, healthy tunnel,
route configuration, host redirects and local service health. Those values are
not fabricated. The command worker and public web service remain unavailable
if that verification does not pass.

## 21. Exit decision

G2.6 is complete only when G2.6A through G2.6D pass, the full existing product
regression remains green, Fleet DMA is the sole production portal tenant, and
the live appliance produces durable verification evidence.

The next gate, G2.7, may begin only after that evidence exists and the Fleet
owner separately approves onboarding the first external client.
