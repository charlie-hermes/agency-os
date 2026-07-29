# Agency OS live production status

- **Status:** LIVE PRODUCTION
- **Verified:** 2026-07-29 UTC
- **Accepted Agency OS release:** `12fde672397e2623ca9a7e0d37b3bbaab4e9dc0a`
- **Accepted VM integration release:** `36601d0b6ccde25ed9f77aec1feb3c07ad0bf5e8`

## What passed

- The complete Agency OS repository gate passed with 144 tests.
- All 12 Hermes roles started in fresh sessions and loaded the exact reviewed
  `AGENTS.md` and `SOUL.md` files.
- Every role completed its allowed action, refused its forbidden action, and
  respected its role boundary.
- The live Core workflow completed eight Paperclip tasks.
- The live Social workflow completed five Paperclip tasks.
- Paperclip created and approved the exact publication packages.
- Buzz created the required private channels and decision messages.
- The workflow completed a real reject, revise and pass cycle.
- The publishing gateway completed three safe mock publications and made no
  real provider write.
- A second Paperclip company completed a separate eight-task Core workflow.
- A cross-brand Social attempt was refused before it created any task.
- The read-only operator portal was active on the VM loopback interface.
- The secret audit passed.
- The VM had zero failed systemd units.

The final VM verifier reported:

```text
PLATFORM: PASS
FUNCTIONAL ACCEPTANCE: PASS
AGENCY OS: LIVE
SECRET AUDIT: PASS
SYSTEMD FAILED UNITS: 0
PRODUCTION: READY
```

## What “live production” means

Hermes, Paperclip, Buzz, the 12 Agency OS roles, the controlled workflows, and
the operator portal are installed and working together on the production VM.
Paperclip is the task and approval authority. Buzz is the private discussion
channel. The operator portal is read-only.

External CMS, analytics, Search Console, keyword-data, social, creative, and
CRM accounts are not falsely marked as connected. Those services use safe
manual handoffs until the owner supplies a real account, scoped credential and
destination and approves that individual connection.

The successful acceptance workflow uses mock publication, so it cannot make an
accidental public provider change. Real client publication still requires an
exact Paperclip approval and an explicitly configured provider connection.

VM backup and disaster recovery are managed directly by the human VM owner.
They are outside the Agency OS completion gate and do not block this status.
