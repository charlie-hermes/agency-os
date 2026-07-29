# Fleet Brand Agent G2.5 release contract

## Outcome

The G2.5 Brand Agent is a private Fleet service over the approved Living Brand
Twin. It is not a general chatbot and it is not the G2.6 client portal.

The first release is deliberately narrow:

- seven approved public Fleet claims may ground answers;
- every factual answer cites an immutable claim, evidence record, source and
  Brand Twin profile checksum;
- unsupported questions return unknown rather than a guess;
- protected information and attempts to change instructions are refused;
- the answer composer is deterministic and provider-independent;
- MCP and JSON APIs are versioned, authenticated and loopback-only;
- transcript text is opt-in and expires; metadata mode stores hashes only;
- the only action creates a cancellable Paperclip human-follow-up task after
  exact human confirmation; and
- the action sends no email, message, publication or other external write.

## Why the first composer is deterministic

The installed Hermes runtime is controlled through Paperclip role containers.
It is not a general web-service model endpoint. G2.5 does not bypass that
boundary or silently add a new provider credential.

The deterministic composer makes the Brand Agent useful now and proves the
hard part: approved retrieval, citations, uncertainty, refusal, isolation,
transcript policy and controlled action. A later model adapter may improve
language quality only after its output passes the same evaluation set. A failed
model or adapter evaluation blocks release.

## Authority boundaries

Paperclip remains authoritative for work, approval and closure. Brand
Intelligence remains authoritative for approved brand truth. Fleet tenancy
controls whether the Brand Agent and controlled action are enabled. The Brand
Agent audit database holds interaction hashes, consented text and action
receipts, but cannot create brand truth.

The service identity is `brand-agent-service`. It has read access to the exact
Fleet tenant and Brand Twin only. It is not one of the 12 production content
roles and has no content publication capability.

## Public truth projection

The public projection is an explicit allowlist in
`config/fleet-brand-agent.json`. The internal Fleet DMA Paperclip company UUID
is excluded even though it is an approved operational claim. Approval alone
does not make a record client-safe.

## Threat model and controls

| Threat | Control |
|---|---|
| Direct prompt injection or goal hijack | user input is never authority; known injection and boundary requests are refused; no model-selected tools |
| Indirect prompt injection | source extracts are treated as inert data and are never executed as instructions |
| Unsupported claims | answers can only be assembled from allowlisted active approved claims |
| Cross-tenant disclosure | exact `brand_id`, tenant entitlement and database keys are checked on every read |
| Secret or internal-data disclosure | protected-request refusal plus an explicit public-claim projection |
| Tool misuse | only four fixed MCP tools exist; tool order and strict schemas are deterministic |
| Action confusion | prepare and confirm are separate; the exact checksum has a short-lived HMAC confirmation token |
| Duplicate action | idempotency key is durably bound to the exact manifest checksum |
| Uncertain action result | the receipt enters an unknown state and replay is blocked pending reconciliation |
| Irreversible action | the first action is a Paperclip task only and can be cancelled by receipt |
| Transcript or memory poisoning | no transcript becomes Brand Twin truth; text retention is consented and expires |
| DNS rebinding or browser abuse | loopback binding, exact Host and Origin checks, bearer authentication and CSP |
| Resource abuse | 64 KiB request limit, bounded text fields and per-route rate limiting |

The controls align with the July 2026 OWASP agentic risk focus on goal hijack,
tool misuse, identity abuse, memory poisoning and unexpected execution, and
with the NIST Govern, Map, Measure and Manage lifecycle. The server follows the
stable MCP `2025-11-25` message model while accepting and validating the newer
mirrored `Mcp-Method` and `Mcp-Name` HTTP headers when clients send them.

## Release evidence

The release is blocked unless every criterion in
`acceptance/fleet-brand-agent.json` passes. Live activation additionally
requires:

1. exact Paperclip approval of the release manifest;
2. enabled `brand_agent` and `controlled_actions` entitlements;
3. an owner-only API key and action secret outside the repository;
4. a healthy loopback service;
5. live factual, unknown, protected and injection evaluations;
6. one confirmed follow-up task followed by cancellation;
7. a checksummed receipt and proof file; and
8. confirmation that no external model or provider write occurred.
