# Repository operating contract

This repository is the controlled specification and fictional reference
implementation for Agency OS.

- Keep Paperclip authoritative for task, approval, budget, dependency, and
  closure state.
- Keep Buzz collaboration non-authoritative.
- Require `brand_id` on every business record and enforce the boundary in code.
- Keep public content structurally separate from internal notes.
- Bind QA, approval, publication manifests, and receipts to canonical SHA-256
  checksums.
- Route external writes through the action gateway with exact destination,
  operation, schedule, approval, and idempotency bindings.
- Use fictional data and mock destinations until a separately approved
  production activation.
- Do not claim that copied role contracts are installed or active. Runtime
  bundles and load evidence are required for activation.

Run the complete repository gate with:

```bash
./scripts/verify
```
