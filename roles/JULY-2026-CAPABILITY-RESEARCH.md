# July 2026 Agent Capability Research and Contract Decisions

**Research cut-off:** 24 July 2026
**Scope:** All 12 Digital Marketing Agency agent roles
**Purpose:** Convert current agent, search, creative, publishing and measurement
capabilities into build-ready role requirements without turning provider
marketing into platform architecture.

## Executive decision

The 12-role design remains sound. The important 2026 upgrade is not to give
every agent more tools. It is to give each agent the smallest useful set of
typed, tenant-scoped capabilities and make every observation, decision and
external action traceable.

The refactored platform should therefore add five shared foundations:

1. a versioned Capability Registry that separates discovery from admission;
2. evidence artifacts that preserve source, freshness, scope and checksum;
3. a policy-enforced action gateway for every state-changing call;
4. content-minimising traces that connect Paperclip work to tools and receipts;
5. release-blocking adversarial evaluations for hostile content, authority
   replay, tenant leakage and uncertain external-write outcomes.

Paperclip remains the authority for work, dependencies, budgets, approvals and
closure. Buzz remains collaboration only. MCP is an integration protocol, not
an authority system. No tool, model, connector, agent card or retrieved
document may create its own permission.

## Research method

Three specialist research tracks reviewed the role library:

- platform, infrastructure, implementation and independent assurance;
- brand intake, research, production and search/answer optimisation;
- visual production, editorial QA, social, publishing and growth measurement.

The research preferred first-party specifications and official product
documentation. Every proposed service was assessed for:

- material benefit to the role;
- read, draft, write or destructive authority;
- account, app-review and consent prerequisites;
- tenant and data exposure;
- cost, quota, maturity and provider volatility;
- evidence and reconciliation behaviour;
- whether a human decision must remain mandatory.

The resulting changes belong mainly in `AGENTS.md`. All 12 `SOUL.md` files were
reviewed and intentionally left unchanged because identity, judgement and voice
are stable; provider features and operational controls are not.

## Platform-wide 2026 operating model

### 1. Capability access is declared, not inferred

A credential, installed MCP server or available SDK does not prove an agent may
use it. Every permitted capability must have an active registry entry for the
exact role, `brand_id`, account, environment, data class and action class.

### 2. Retrieval produces evidence artifacts

Exa, Firecrawl, search APIs, analytics APIs, CMS records and uploaded documents
may locate or extract useful information. Their output is untrusted input until
it is recorded, scoped and evaluated. Material evidence should retain:

- source and publisher/owner;
- retrieval and publication/update times;
- query, dimensions, filters and data window where applicable;
- public, internal, restricted or prohibited usage scope;
- licence, quotation, attribution and retention limits;
- source snapshot, response or content checksum;
- freshness basis and expiry;
- linked claim and entity IDs;
- limitations and whether the item is fact, measurement, model inference or
  recommendation.

### 3. External writes cross one governed boundary

Publication, scheduling, CMS mutation, DAM upload, design-file modification and
similar state changes must pass through an approved adapter and action gateway.
The gateway, not the agent prompt, enforces:

- authenticated workload identity rather than an agent-supplied principal;
- tenant, role, account and environment;
- exact artifact or manifest checksum;
- destination and allowed fields;
- approval reference and expiry;
- policy decision and budget state;
- idempotency key;
- canonical request binding, dispatch-time revalidation and a single-use
  decision receipt;
- credential binding;
- timeout, reconciliation and rollback behaviour.

### 4. Observability does not become a client-data warehouse

Traces should correlate the Paperclip issue, campaign, brand, artifact checksum,
capability, policy decision and external receipt. Raw prompts, client files,
tool arguments, tool results, credentials and secret-bearing headers stay out
of ordinary telemetry. Diagnostic content capture is an exceptional,
tenant-scoped, redacted and retention-bounded mode.

### 5. Graceful degradation is visible

When a capability is unavailable, out of budget, unapproved or returning
uncertain data, the agent creates an explicit Paperclip handoff, retry state or
blocker. It may not silently select a broader tool, different provider,
different account, weaker evidence source or changed destination.

## Role-by-role frontier capability decisions

### 1. Hermes Agency Director

**2026 role:** business-workflow orchestrator and capability-bundle selector.

**Material upgrades**

- Select only admitted capabilities for the assigned brand and action class.
- Treat retrieved content, MCP metadata and external agent descriptions as
  untrusted.
- Require a policy-decision receipt for external actions.
- Own visible degradation, budget and human-handoff states.
- Track end-to-end evidence and adversarial-evaluation completeness.

**Best capabilities**

- Paperclip task and approval adapter: adopt now.
- Buzz collaboration adapter: adopt now, non-authoritative.
- Capability Registry and policy decision read: adopt now.
- A2A external-agent adapter: pilot only; never a second task ledger.

### 2. Codex Technical Implementation Specialist

**2026 role:** builder of typed adapters, enforcement points and evaluation
fixtures.

**Material upgrades**

- Maintain a provider-neutral capability registry and typed MCP/API adapters.
- Validate schemas, endpoint identity, OAuth audience/scopes, timeouts, costs
  and fallback behaviour.
- Route writes through one policy-enforced action gateway.
- Propagate safe trace context across Paperclip, Buzz, model, tool and external
  calls.
- Generate and expose supply-chain artifacts.
- Maintain fictional-tenant prompt-injection, replay, retry and failure tests.

**Best capabilities**

- Codex AGENTS/skills/MCP/subagent facilities: adopt as the controlled coding
  harness available to this role.
- MCP 2025-11-25 typed tools and OAuth rules: adopt as adapter standard.
- MCP sampling, elicitation, Roots and Tasks: default-deny; admit separately
  with consent, mount/data, time, cost, cancellation and handoff controls.
- OpenTelemetry GenAI conventions: adopt selectively with content off.
- A2A: pilot only for external independently operated agents.
- Temporal: watch; consider only beneath Paperclip for a proven execution
  durability gap.

### 3. Platform Assurance Reviewer

**2026 role:** independent evidence and adversarial release gate.

**Material upgrades**

- Verify admitted capabilities, policy decisions, egress, trace redaction and
  fallback behaviour against the running candidate.
- Test indirect prompt injection and changed MCP/A2A metadata.
- Prove approval/policy receipts cannot be replayed across brands, checksums or
  destinations.
- Verify SBOM, provenance and signatures against the deployed digest.
- Distinguish stable standards from experimental protocol features.

**Best capabilities**

- Provider-neutral evaluation fixtures: adopt now.
- OPA decision tests and denied-path probes: adopt now.
- Cosign/SBOM/provenance verification: adopt now.
- OpenTelemetry trace inspection: adopt now with redaction validation.

### 4. Brand and Brief Steward

**2026 role:** authority-preserving multimodal intake and brand evidence
custodian.

**Material upgrades**

- Create a Source Intake Record for files, pages and system records.
- Preserve originals separately from OCR/model extraction.
- Maintain a source-backed Entity Ledger for approved names and relationships.
- Treat embedded document instructions as hostile content.
- Apply freshness, usage, licence and confidentiality rules before public use.

**Best capabilities**

- Approved read-only CMS/DAM/CRM adapters: controlled pilot.
- Firecrawl Parse/schema extraction: controlled pilot for authorised,
  non-restricted sources.
- Business Profile and Merchant read views: pilot only for eligible brands.

### 5. Search and Content Strategist

**2026 role:** evidence-led opportunity selector and information-gain designer.

**Material upgrades**

- Type observations as first-party measurement, platform measurement,
  retrieved page, client evidence, model inference or recommendation.
- Prefer first-party property/account data over generic tool scores.
- Require a falsifiable information-gain hypothesis for each commissioned
  asset.
- Separate Exa/Firecrawl discovery from the underlying source that supports a
  claim.
- Apply freshness checks and reopen work when material evidence changes.

**Best capabilities**

- Google Ads Keyword Plan ideas/historical metrics: adopt when connected.
- Search Console Search Analytics: adopt when connected.
- GA4 Data API: adopt when connected.
- Exa Search/Contents/Find Similar/verticals: controlled pilot.
- Exa Answer: discovery aid only, never the final source.
- Firecrawl map/crawl/scrape/extract: controlled pilot for authorised targets.
- Google Trends API: watch while alpha/limited access.
- GBP/Merchant read views: specialist pilot for local/commerce clients.

### 6. Content Producer

**2026 role:** evidence-bound, original canonical asset creator.

**Material upgrades**

- Produce a Claim-to-Evidence Map and Information-Gain Ledger.
- Record whether each source supports a fact, quote, paraphrase, transformation
  or inspiration only.
- Treat retrieval output as untrusted and return material gaps to the evidence
  owner.
- Never simulate first-hand experience, testing or expert review.

**Best capabilities**

- Approved Research Evidence Pack and Brand Source Library: adopt now.
- Read-only DAM reference: controlled pilot.
- Broad autonomous web research: do not grant by default.

### 7. Search and Answer Optimiser

**2026 role:** reader-first search/answer optimisation plus typed technical
evidence.

**Material upgrades**

- Separate draft/static checks, published live checks, indexed platform state,
  field measurements and lab audits.
- Treat CrUX as aggregated real-user field data and Lighthouse as lab data.
- Record URL Inspection, canonical, robots, structured-data and validator
  evidence with limits.
- Keep all URL submission and property mutation outside this role.
- Make no AI-citation, indexing, ranking or rich-result guarantee.

**Best capabilities**

- Search Console URL Inspection and Search Analytics: adopt when connected.
- CrUX API: adopt where data exists.
- Lighthouse: adopt for reproducible lab checks.
- PageSpeed Insights: secondary/pilot.
- Rich Results and structured-data validation: adopt with visible-content
  review.
- Bing Webmaster diagnostics: controlled pilot.
- IndexNow and Google indexing writes: not for this role.

### 8. Visual and Creative Specialist

**2026 role:** provenance-aware creative producer working against live design
systems and deterministic asset lineage.

**Material upgrades**

- Bind inputs, prompts/templates, provider job, original and child hashes.
- Distinguish read-only design context from design/DAM/generation writes.
- Record rights, consent, territory, expiry and C2PA status.
- Validate dimensions, crop-safe areas, contrast and responsive visual diffs.
- Never treat generated/composited imagery as documentary evidence.

**Best capabilities**

- Figma remote MCP: scoped pilot, read-only by default.
- Adobe Firefly Services: provider-neutral generation/edit pilot.
- Canva Connect: controlled template/export pilot.
- Cloudinary or approved DAM: controlled immutable master/rendition pilot.
- C2PA verification: adopt now; signing is a later governed pilot.
- Playwright visual comparisons and WCAG 2.2 checks: adopt now.

### 9. Editorial Integrity QA

**2026 role:** independent claim-evidence, provenance, accessibility and
rendering gate.

**Material upgrades**

- Verify the exact proposition, qualifier, scope and freshness supported by
  each source rather than checking that a citation exists.
- Require deterministic link, schema, checksum, C2PA, accessibility and visual
  evidence where applicable.
- Treat model-generated flags as leads only; no model can issue PASS.
- Verify public copy, structured data and visual children describe the same
  approved candidate.

**Best capabilities**

- Internal claim/source graph and snapshot validator: adopt now.
- JSON Schema/checksum/link validation: adopt now.
- C2PA verification: adopt now.
- axe/Playwright reports plus human review: adopt now.
- Rich Results/schema validation plus visible-content review: adopt now.

### 10. Social Amplifier

**2026 role:** channel-native package designer, not publisher or engagement bot.

**Material upgrades**

- Require a destination-specific capability record including account type,
  approved verbs, app/access tier, consent, limits and expiry.
- Bind copy, media-child checksums, disclosures, previews, tracking, schedule,
  test hypothesis and approval expiry.
- Separate organic social from paid media and audience tools.
- Prohibit comments, DMs, replies, scraping, broad listening, audience upload
  and spend by default.

**Best capabilities**

- Owned-account analytics: pilot per platform.
- LinkedIn Community Management/Posts: publishing adapter pilot after partner
  requirements; not this agent's write tool.
- Meta/Instagram professional-account publishing: adapter pilot after account
  and app review.
- YouTube analytics: read adopt; publishing pilot belongs to Publishing.
- TikTok direct posting: watch-to-pilot after app audit and explicit creator
  consent.
- X and social-listening services: reject as defaults; assess per client.

### 11. Publishing Operator

**2026 role:** exact-version external action operator and reconciliation owner.

**Material upgrades**

- Use only an approved destination adapter; never a generic browser, UI, SDK or
  write-capable MCP as an escape path.
- Track `REQUESTED`, `ACCEPTED`, `PROCESSING`, `SCHEDULED`, `PUBLISHED`,
  `FAILED` and `UNKNOWN` distinctly.
- Persist idempotency before writing; reconcile ambiguous or partial results
  before retry.
- Verify webhook authenticity, replay window and deduplication.
- Independently retrieve and compare rendered output with the approved
  manifest.

**Best capabilities**

- One preview/reconcile-capable CMS adapter per client: controlled pilot.
- Contentful or WordPress are candidates, not universal architecture.
- LinkedIn and Meta/Instagram organic adapters: controlled pilot.
- YouTube publish adapter: controlled pilot with quota/account audit.
- Direct generic write-capable MCP/browser automation: reject.

### 12. Growth Intelligence Analyst

**2026 role:** read-only measurement diagnostician with explicit attribution
and causal limits.

**Material upgrades**

- Record source surface, reporting identity, attribution window, consent,
  sampling/modeling, freshness and late-arriving data.
- Connect publication receipt to campaign key, event definition, conversion and
  approved CRM stage.
- Classify conclusions as descriptive, attributed, experimentally incremental,
  modeled causal or insufficient evidence.
- Require pre-registration, power/holdout logic and human approval for tests.
- Hold no admin, publishing, conversion import, audience export or CRM write
  permissions.

**Best capabilities**

- GA4 Data API and Search Console read: adopt when connected.
- Platform/YouTube read analytics: controlled pilots.
- Per-brand read-only BigQuery: pilot with query and privacy limits.
- Read-only CRM measurement view: controlled pilot.
- Meridian MMM and GeoLift: later specialist pilots with qualified human
  oversight.
- Enhanced/offline conversions, platform Conversions APIs and consent
  configuration: Technical Implementation only after privacy/legal approval.

## Capability portfolio

| Capability family | Decision | Reason |
|---|---|---|
| Capability Registry and policy gateway | Adopt now | Required to make tool access auditable and enforceable |
| Paperclip and typed Buzz adapters | Adopt now | Preserve authoritative state and focused collaboration |
| MCP typed adapter standard | Adopt now | Current interoperable tool boundary; discovery is not admission |
| OPA policy decisions | Adopt now | Deterministic allow/deny/approval requirement at the action boundary |
| Content-minimising OpenTelemetry | Adopt now | End-to-end traceability without making traces a content store |
| SLSA/SBOM/Sigstore verification | Adopt now | Bind deployed artifacts to source/build evidence |
| Exa and Firecrawl retrieval | Pilot | High research benefit; content, spend and retention risks require controls |
| GSC, GA4, Ads and CrUX reads | Adopt when connected | Strong first-party and platform evidence |
| Google Trends API | Watch | Alpha and limited-access; interest is not volume |
| Figma, Firefly, Canva and DAM | Pilot | Powerful native creative workflow with material write/rights exposure |
| C2PA verification | Adopt now | Useful provenance evidence, not truth or rights proof |
| CMS and social publishing adapters | Pilot per destination | Different account, approval, state and reconciliation rules |
| A2A | Pilot at external boundary | Useful interoperability; must not become a task authority |
| Agent Vault | Synthetic pilot | Useful secret minimisation but still research preview and not isolation |
| Temporal | Watch | Only justified by a measured execution-durability gap |
| Social listening, engagement automation and broad scraping | Reject by default | Privacy, coverage, platform-policy and authority risks |
| Autonomous ad spend, audience upload or conversion writes | Reject for these roles | Human, privacy and technical implementation authority remains mandatory |

## Mandatory shared artifact upgrades

The builder should extend the parent blueprint schemas with:

- `SourceIntakeRecord`;
- `SourceObservation`;
- `EntityLedger`;
- `ResearchEvidencePack`;
- `ClaimEvidenceMap`;
- `InformationGainLedger`;
- `TechnicalEvidenceRecord`;
- `VisualManifest`;
- `QAEvidenceManifest`;
- `ChannelCapabilityRecord`;
- `DestinationCapabilityRecord`;
- `PublicationManifest`;
- `PublicationReceipt`;
- `MeasurementManifest`;
- `ExperimentManifest`;
- `PolicyDecisionReceipt`.

All manifests must use canonical serialization before hashing. A root checksum
must bind all public copy and media children. Material changes create a new
candidate, QA verdict and approval when applicable.

## Mandatory adversarial acceptance tests

Before activation, prove:

1. hidden instructions in a client file, webpage or tool result cannot change
   scope, permission, destination or approval;
2. changed MCP tool metadata/schema or an A2A Agent Card is rejected or
   quarantined;
3. Brand A cannot retrieve, trace, publish to or use Brand B's capabilities;
4. a policy/approval receipt cannot be replayed for another checksum,
   destination, task, budget or brand;
5. a worker cannot bypass the credential broker or reach metadata/private/admin
   endpoints;
6. tool/provider failure creates a visible Paperclip handoff or block rather
   than silent substitution;
7. ordinary traces contain no prompt, raw client source, secret or sensitive
   tool payload;
8. a generated visual, absent/invalid C2PA record or ambiguous rights state
   cannot pass as documentary/approved creative;
9. automated accessibility and schema checks cannot produce a QA PASS alone;
10. duplicate webhooks, timeouts and partial batch results cannot cause
    duplicate publication;
11. a rendered CMS/social result is reconciled against the exact approved
    manifest;
12. source-surface differences, consent loss and late data prevent unsupported
    causal claims.

## SOUL.md review verdict

| Role | Verdict | Reason |
|---|---|---|
| Agency Director | No change | Capability governance is operational, not identity |
| Technical Implementation Specialist | No change | Existing builder identity remains correct |
| Platform Assurance Reviewer | No change | Existing independent, sceptical posture remains correct |
| Brand and Brief Steward | No change | Truth, uncertainty and confidentiality anchors remain correct |
| Search and Content Strategist | No change | Evidence-led opportunity judgement remains correct |
| Content Producer | No change | Reader value and evidence discipline remain correct |
| Search and Answer Optimiser | No change | Reader-first optimisation and no-guarantee posture remain correct |
| Visual and Creative Specialist | No change | Brand-safe, truthful creative identity remains correct |
| Editorial Integrity QA | No change | Independent truth-protection identity remains correct |
| Social Amplifier | No change | Channel-native adaptation without truth dilution remains correct |
| Publishing Operator | No change | Exact, cautious external execution identity remains correct |
| Growth Intelligence Analyst | No change | Causal humility and business relevance remain correct |

## Primary source register

### Agent protocols, security and platform engineering

- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [MCP Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [MCP Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
- [A2A Protocol v0.3.0](https://a2a-protocol.org/v0.3.0/specification/)
- [OpenTelemetry GenAI attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [Open Policy Agent documentation](https://www.openpolicyagent.org/docs)
- [OWASP Agentic Security Initiative](https://genai.owasp.org/initiatives/agentic-security-initiative/)
- [SLSA v1.2](https://slsa.dev/spec/v1.2/)
- [Sigstore/Cosign verification](https://docs.sigstore.dev/cosign/verifying/verify/)
- [CycloneDX specification](https://cyclonedx.org/specification/overview/)
- [Docker rootless mode](https://docs.docker.com/engine/security/rootless/)
- [Infisical Agent Vault status and architecture](https://infisical.com/blog/agent-vault-the-open-source-credential-proxy-and-vault-for-agents)

### Research, search and analytics

- [Exa documentation](https://exa.ai/docs/llms.txt)
- [Firecrawl documentation](https://docs.firecrawl.dev/llms.txt)
- [Google AI-search optimization guide](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)
- [Google Ads keyword ideas](https://developers.google.com/google-ads/api/docs/keyword-planning/generate-keyword-ideas)
- [Search Console API](https://developers.google.com/webmaster-tools)
- [Search Console URL Inspection](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [GA4 Data API](https://developers.google.com/analytics/devguides/reporting/data/v1)
- [Google Trends API alpha](https://developers.google.com/search/apis/trends)
- [CrUX API](https://developer.chrome.com/docs/crux/api/)
- [Lighthouse](https://developer.chrome.com/docs/lighthouse/)
- [Google structured-data policies](https://developers.google.com/search/docs/appearance/structured-data/sd-policies)
- [Google Business Profile APIs](https://developers.google.com/my-business)
- [Google Merchant API](https://developers.google.com/merchant/api/reference/rest)
- [W3C PROV-O](https://www.w3.org/TR/prov-o/)

### Creative, publishing and measurement

- [Figma MCP Server](https://developers.figma.com/docs/figma-mcp-server/)
- [Adobe Firefly Services API](https://developer.adobe.com/firefly-services/docs/firefly-api/api/)
- [Canva Connect APIs](https://www.canva.dev/docs/connect/)
- [C2PA specifications](https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html)
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots)
- [Contentful API basics](https://www.contentful.com/developers/docs/references/api-basics/)
- [WordPress REST API](https://developer.wordpress.org/rest-api/reference/)
- [LinkedIn Community Management](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview?view=li-lms-2026-06)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [Meta Instagram API official collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)
- [TikTok Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [YouTube Data API videos](https://developers.google.com/youtube/v3/docs/videos)
- [YouTube Analytics and Reporting APIs](https://developers.google.com/youtube/analytics)
- [GA4 and BigQuery comparison](https://support.google.com/analytics/answer/13578783)
- [Google Consent Mode](https://developers.google.com/tag-platform/security/guides/consent)
- [Google Meridian](https://developers.google.com/meridian)
- [Meta GeoLift](https://facebookincubator.github.io/GeoLift/docs/intro/)

## Revalidation rule

This document records a July 2026 research decision, not a permanent provider
guarantee. Before wiring a provider, the builder must re-check its current
official documentation, availability, access tier, scopes, pricing, quotas,
data handling, deprecation policy and account eligibility. Those live facts
belong in the Capability Registry, not in `SOUL.md`.
