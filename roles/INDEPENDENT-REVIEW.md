# Independent Review Record

**Review date:** 2026-07-24
**Scope:** Every `AGENTS.md` and `SOUL.md` in this library, plus the July 2026 research record and Capability Registry specification
**Method:** Three independent high-reasoning research tracks, divided by platform, strategy/content and delivery/measurement roles. Each track implemented its role-contract amendments; a different track then cross-reviewed every amended role. Precise P0/P1 findings were corrected and the same reviewers performed closure checks.

## Final verdict

All 12 role pairs pass. No P0 or P1 issue remains.

The shared `JULY-2026-CAPABILITY-RESEARCH.md` and
`CAPABILITY-REGISTRY-SPEC.md` also pass. This remains a design and contract
verdict, not proof of installation or enforcement on the new VM.

| Role | AGENTS.md | SOUL.md |
|---|---|---|
| Hermes Agency Director | PASS | PASS |
| Codex Technical Implementation Specialist | PASS | PASS |
| Platform Assurance Reviewer | PASS | PASS |
| Brand and Brief Steward | PASS | PASS |
| Search and Content Strategist | PASS | PASS |
| Content Producer | PASS | PASS |
| Search and Answer Optimiser | PASS | PASS |
| Visual and Creative Specialist | PASS | PASS |
| Editorial Integrity QA | PASS | PASS |
| Social Amplifier | PASS | PASS |
| Publishing Operator | PASS | PASS |
| Growth Intelligence Analyst | PASS | PASS |

## What the reviews tested

- clear mission, ownership and prohibitions;
- inputs, outputs and definitions of done;
- authority and self-approval boundaries;
- Paperclip task-state authority and Buzz collaboration limits;
- exact artifact, checksum and approval handoffs;
- tenant isolation, secret handling and external-action safety;
- failure, escalation, retry and recovery behaviour;
- role separation across infrastructure, implementation, strategy, production, QA, social, publishing and measurement;
- focused, stable, model-agnostic SOUL files without workflow bloat.

## Material improvements made during review

- Removed a new-brand onboarding deadlock and clarified when the immutable `brand_id` is created.
- Confined adversarial platform tests to authorised synthetic or sandbox targets by default.
- Clarified Docker desired-state ownership versus privileged VM runtime ownership.
- Named the authorities that approve facts and content strategy.
- Added source usage, licensing and public/private restrictions.
- Defined Draft, Complete and QA-Passed Asset Packages unambiguously.
- Preserved claim and source lineage through search and answer optimisation.
- Added structured escalation records and QA finding ownership.
- Defined a dedicated social QA mode and exact Approval Record checks.
- Bound bundled social approval to every child copy and visual checksum.
- Bound publication to an approved Publication Manifest.
- Added an `UNKNOWN` external-write state that prohibits unsafe retries.
- Strengthened visual rights escalation, tracking-repair behaviour and cross-brand learning controls.
- Made publication receipts identify the exact approval, manifest and adapter transformation used.

## July 2026 capability-review improvements

- Added a provider-neutral Capability Registry; discovering a tool, credential
  or external agent no longer implies admission or permission.
- Added typed evidence artifacts, source freshness, usage scope, information
  gain, claim lineage, entity identity and technical-observation records.
- Required authenticated workload identity, a canonical action-request binding,
  dispatch-time policy revalidation and a single-use decision receipt.
- Made MCP tools typed and allowlisted, with sampling, elicitation, Roots and
  Tasks default-denied and separately governed.
- Restricted A2A to an authenticated, non-authoritative external boundary with
  card/schema/identity drift detection.
- Added runtime egress, metadata-endpoint, telemetry privacy and deployment
  supply-chain controls.
- Added exact creative lineage, rights/consent evidence, C2PA limitations,
  accessibility evidence and responsive visual comparison.
- Strengthened QA from “citation exists” to exact claim-evidence support,
  qualifier, scope and freshness.
- Added destination capability records, exact Publication Manifests, an
  adapter-only publishing path, `UNKNOWN` reconciliation and webhook
  authenticity/replay controls.
- Required read-only measurement access, source-surface semantics, consent and
  attribution limits, experiment pre-registration and explicit causal
  conclusion classes.
- Added cross-brand, prompt-injection, approval replay, time-of-check/time-of-use,
  tool drift, telemetry and external-write adversarial tests.

## July 2026 closure verdicts

| Review group | Roles independently cross-reviewed | Closure |
|---|---|---|
| Platform reviewer | Brand Steward, Strategist, Producer, Optimiser | PASS |
| Strategy/content reviewer | Visual, Editorial QA, Social, Publishing, Growth | PASS |
| Delivery/measurement reviewer | Director, Technical, Platform Assurance, shared registry | PASS |

All initial cross-review findings were resolved. The final closure reviews
reported no remaining P0 or P1 issue.

## SOUL.md decision

All 12 `SOUL.md` files remain intentionally unchanged. The research found no
missing stable identity, voice or judgement anchor. API versions, provider
features, capability permissions, evidence schemas and external-action controls
belong in `AGENTS.md`, runtime configuration and the Capability Registry.

## Installation note

This review validates the role-contract design. It does not claim that the files are installed, loaded or enforced on the new VM. The builder must complete the installation and runtime verification steps in `README.md` and the parent implementation blueprint.

## Closed-loop learning review addendum

All 12 `AGENTS.md` contracts were extended with role-specific learning and
mistake-prevention loops. A separate high-reasoning reviewer checked the final
contracts, their corresponding `SOUL.md` files, the Codex activation brief and
the parent blueprint.

The review verified that:

- every role retrieves only active, validated and correctly scoped learning;
- every role records evidence-linked failures and proposes candidate learning;
- known failed approaches cannot be repeated unchanged without new evidence and
  an explicit authorised exception;
- specialists cannot activate, promote, retire or share durable guidance;
- the Hermes Agency Director owns final learning disposition;
- Growth Intelligence supplies measurement without self-promoting causal
  conclusions;
- QA and Platform Assurance preserve independent current-candidate review;
- publishing memory cannot authorise a retry or external action;
- brand-only learning remains tenant-scoped;
- missing, corrupted, expired, superseded, unvalidated and wrong-brand memory
  cannot silently guide work.

**Verdict:** PASS for all 12 role contracts. No P0 or P1 issue remained. Two
non-blocking terminology differences in the shared documents were corrected
before archive creation.
