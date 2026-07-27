# Agency OS

Agency OS is a contract-first, tenant-isolated reference implementation of the
Hermes + Paperclip digital marketing agency design.

This initial Phase 0/1 release deliberately does three things:

1. preserves the verified 12-role source library and implementation blueprint;
2. reconciles the role, artifact, approval, learning, security, and operations
   contracts into one authoritative build specification; and
3. proves one fictional article flow through a local, fail-closed publication
   gateway.

It does **not** install agents on a VM, connect real client data, call a real
provider, or publish externally.

## Repository map

- `roles/` — the 12 reviewed `SOUL.md` / `AGENTS.md` role pairs and their source
  supporting documents.
- `docs/reference/implementation-blueprint.md` — the byte-preserved supplied
  blueprint.
- `docs/contract-reconciliation.md` — authoritative resolutions where the
  blueprint and role library differ.
- `docs/master-plan.md` — living gate-by-gate roadmap from the current
  fictional controls through the complete production system.
- `docs/security-operations.md` — enforceable Phase 0/1 controls and production
  promotion blockers.
- `schemas/` — versioned JSON Schemas for lifecycle and learning records.
- `agency_os/` — standard-library-only reference controls, including an
  authoritative capability registry and an injectable in-memory or durable
  local-process action ledger, verified fictional runtime identity, mock
  credential broker and deny-by-default mock egress boundary.
- `fixtures/` — fictional tenant input.
- `acceptance/matrix.json` — release criteria mapped to executable evidence.
- `tests/` — allowed-path, denied-path, recovery, and vertical-slice tests.

## Verify

```bash
./scripts/verify
```

Run the fictional demonstration:

```bash
python3 -m agency_os.demo
```

The demonstration writes only to in-memory stores and a local mock destination.
