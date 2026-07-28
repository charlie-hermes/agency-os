# Agency OS

Agency OS is a contract-first, tenant-isolated reference implementation of the
Hermes + Paperclip digital marketing agency design.

This fictional reference deliberately does five things:

1. preserves the verified 12-role source library and implementation blueprint;
2. reconciles the role, artifact, approval, learning, security, and operations
   contracts into one authoritative build specification;
3. proves one fictional article flow through a local, fail-closed publication
   gateway; and
4. begins Gate 5 with a protected local Platform Authority host, principal-bound
   worker clients, durable typed tasks, versioned approver policy, host-attested
   approvals, deadline-enforced restartable collaboration decisions, tenant
   evidence/artifact durability, a fictional durable lease/retry queue,
   exact-manifest coordinated local tenant offboarding, and complete attested
   logical tenant export/restore, plus versioned local audit-retention policy
   and content-free tenant telemetry; and
5. admits one exact installed Paperclip/Buzz command contract from read-only,
   checksum-pinned target-host evidence while failing closed on drift.

It does **not** install agents on a VM, make authenticated platform mutations,
connect real client data, call a real provider, or publish externally.

## Repository map

- `roles/` — the 12 reviewed `SOUL.md` / `AGENTS.md` role pairs and their source
  supporting documents.
- `docs/reference/implementation-blueprint.md` — the byte-preserved supplied
  blueprint.
- `docs/contract-reconciliation.md` — authoritative resolutions where the
  blueprint and role library differ.
- `docs/master-plan.md` — living gate-by-gate roadmap from the current
  fictional controls through the complete production system.
- `docs/gate-5-platform-foundation.md` — exact authority boundaries, denial
  evidence and remaining work for the first persistent Gate 5 slice.
- `docs/security-operations.md` — enforceable Phase 0/1 controls and production
  promotion blockers.
- `schemas/` — versioned JSON Schemas for lifecycle and learning records.
- `agency_os/` — standard-library-only reference controls, including an
  authoritative capability registry and an injectable in-memory or durable
  local-process action ledger, a protected fictional gateway/identity host with
  a worker-only IPC client, mock credential broker, deny-by-default mock egress
  boundary, a protected fictional Platform Authority host with principal-bound
  worker clients, typed Buzz context, persistent tenant evidence/artifacts, a
  protected fictional durable work queue, coordinated local offboarding, and
  complete logical tenant authority export/restore with local audit-retention
  governance and tenant-scoped telemetry.
- `config/installed-platforms.json` — non-secret, read-only target-host version,
  executable/unit checksum, service, health, exact API and Buzz command evidence.
- `scripts/verify-installed-platforms` — explicit read-only live drift check.
- `fixtures/` — fictional tenant input.
- `acceptance/matrix.json` — release criteria mapped to executable evidence.
- `tests/` — allowed-path, denied-path, recovery, and vertical-slice tests.

## Verify

The complete repository gate is supported on **Linux only** because the runtime
identity tests intentionally require `SO_PEERCRED` and process facts from a
mounted `/proc` filesystem. The verification script checks these prerequisites
before running any tests. GitHub Actions runs the same gate on Ubuntu; macOS and
Windows are not supported validation hosts for Gate 4.

```bash
./scripts/verify
```

Run the fictional demonstration:

```bash
python3 -m agency_os.demo
```

The demonstration writes only to in-memory stores and a local mock destination.

On the recorded target VM only, re-run the installed-platform admission check:

```bash
./scripts/verify-installed-platforms
```

That command reads package files, systemd properties, the private Paperclip
health endpoint and Buzz help output. It does not send a message or mutate task state.
