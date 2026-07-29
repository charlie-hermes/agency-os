# Fleet client portal G2.6 threat model

- **Scope:** Fleet DMA private portal, Fleet administration, local authorities,
  Launch Room intake and Paperclip decision delivery
- **Production tenants:** Fleet DMA only
- **Trust posture:** fail closed; no client-supplied tenant authority

## Assets

- WorkOS identity and organisation sessions;
- Cloudflare Access assertions;
- tenant, brand, membership and entitlement bindings;
- approved brand sources, facts, claims and content;
- Paperclip work and approvals;
- portal commands, receipts and audit events;
- untrusted Launch Room uploads; and
- service credentials and protected SQLite databases.

## Trust boundaries

1. The public browser reaches only Cloudflare Access.
2. Cloudflare reaches the loopback Next.js service through a private tunnel.
3. The web service holds WorkOS session configuration but no Paperclip secret
   and no protected database access.
4. The web service reaches the portal authority through a Unix socket. The
   authority verifies Linux peer credentials.
5. The command worker alone holds the restricted Paperclip board credential.
6. The ingest worker processes untrusted material without network access and
   cannot approve its own output.

## Threats and controls

| Threat | Required control | Failure behaviour |
|---|---|---|
| forged or missing edge identity | verify Cloudflare Access JWT issuer, audience and JWKS | deny request |
| stolen or stale app session | WorkOS encrypted host-only cookie, 60-minute idle policy, 12-hour absolute policy, session refresh and revocation | redirect or deny |
| wrong customer organisation | exact WorkOS organisation-to-account binding | deny without revealing another tenant |
| host-header tenant switching | exact reviewed host; server-side host, identity and membership join | return not found/denied |
| browser-supplied `brand_id` or `tenant_id` | security context contains no browser-derived tenant key | ignore and deny |
| client becoming Fleet administrator | separate admin hostname, explicit Fleet user allowlist and edge policy | deny |
| cross-tenant object reference | tenant and brand included in every authority query; two-tenant same-label tests | behave as absent |
| CSRF or cross-origin mutation | WorkOS session, exact Origin, Server Action protection, SameSite cookie and step-up authentication | deny without command |
| replayed decision | tenant-scoped idempotency key and canonical payload checksum | return existing command or conflict |
| stale approval | expected checksum/version on every command | `conflict`; require fresh review |
| downstream timeout | durable state changes to `unknown`; reconcile with same idempotency key | never display success |
| Paperclip credential theft from web tier | web process has no Paperclip credential; worker is separate and restricted | no Paperclip access |
| alternate portal approval authority | portal stores delivery commands only; Paperclip readback is authoritative | do not complete projection |
| malicious upload | size limits, extension/magic agreement, ClamAV, macro/archive/executable denial | quarantine/reject |
| parser exploit | no-network ingest service, read-only system, private temp space, bounded tools and output | fail extraction |
| SSRF | HTTPS 443 only, credentials denied, every hop resolved and checked, all non-global IPv4/IPv6 denied | reject URL |
| prompt injection in a source | extracted text remains an untrusted candidate; no tool access; human confirmation required | never auto-admit |
| data exfiltration through client DTO | explicit safe projections; Paperclip UUID and internal notes excluded | test failure blocks release |
| support impersonation abuse | no support impersonation in G2.6; future sessions require reason, expiry and audit | feature absent |
| entitlement drift | append-only versioned grants, exact Fleet DMA production limit | module disabled |
| accidental external-client activation | live entitlement names one production brand and one tenant; provisioning adapter is test-only | deny activation |
| secret in repository or logs | environment-only secrets, systemd credential files, no token output | service refuses missing secret |
| denial of service | edge, session and command rate limits; 64 KiB authority requests; 50 MiB files; timeouts | bounded failure |
| clickjacking or script injection | CSP nonce and strict-dynamic, `frame-ancestors 'none'`, no unsafe script, no object sources | browser blocks content |
| unsafe rollback | mutation kill switch, entitlement suspension and route removal preserve audit | read-only safe state |

## Security invariants

1. A browser cannot choose its account, client brand, tenant, operational brand,
   role, scope or entitlement.
2. A valid WorkOS session without a matching Fleet membership has no access.
3. A valid identity on the wrong hostname has no access.
4. Portal commands do not complete before Paperclip authority is recorded.
5. An uncertain downstream result is never shown as success.
6. An ingest worker cannot turn extracted material into an approved fact.
7. Fleet administration and client navigation are separate surfaces.
8. G2.6 production has exactly one operational tenant: Fleet DMA.

## Deferred threats

Public signup, public APIs, billing webhooks, customer SSO/SCIM, custom domains,
authenticated connectors, support impersonation, external clients and provider
publication are absent. Each requires a new threat-model amendment before it is
enabled.
