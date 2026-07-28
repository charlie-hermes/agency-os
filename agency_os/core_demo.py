"""Run and summarize the complete fictional Core workflow."""

from __future__ import annotations

import json

from .core_workflow import CoreWorkflowResult, run_core_workflow
from .fictional_platforms import (
    InMemoryBuzzTransport,
    InMemoryPaperclipBoardTransport,
    InMemoryPaperclipTransport,
)
from .gateway import MockPublisher
from .integrations import (
    PaperclipBoardApprovalAdapter,
    PaperclipBrandBinding,
    PaperclipLifecycleAdapter,
    TypedBuzzAdapter,
)


def run_fictional_core_workflow() -> CoreWorkflowResult:
    """Compose deterministic transports outside the transport-opaque engine."""

    binding = PaperclipBrandBinding(
        company_id="00000000-0000-4000-8000-000000000001",
        brand_id="brand_lantern",
    )
    paperclip_transport = InMemoryPaperclipTransport(
        company_id=binding.company_id,
        brand_id=binding.brand_id,
    )
    lifecycle = PaperclipLifecycleAdapter(paperclip_transport, binding)
    board = PaperclipBoardApprovalAdapter(
        InMemoryPaperclipBoardTransport(paperclip_transport), binding
    )
    buzz = TypedBuzzAdapter(InMemoryBuzzTransport(), binding.brand_id)

    def approve(requested, manifest):
        return board.decide_approval(
            requested["id"],
            decision="approve",
            decision_note=(
                "Human owner approved exact sandbox manifest "
                f"{manifest['content_checksum']}"
            ),
        )

    return run_core_workflow(
        paperclip=lifecycle,
        buzz=buzz,
        approval_authority=approve,
        publisher=MockPublisher(),
    )


def main() -> None:
    result = run_fictional_core_workflow()
    print(
        json.dumps(
            {
                "brand_id": "brand_lantern",
                "core_roles": list(result.tasks_by_role),
                "paperclip_task_counts": result.operator_projection["task_counts"],
                "paperclip_approval": result.approval["status"],
                "qa_first_verdict": result.records["qa_revise"]["payload"]["verdict"],
                "qa_final_verdict": result.records["published_qa_verdict"]["payload"]["verdict"],
                "buzz_authority": "non_authoritative",
                "publication_state": result.records["published_receipt"]["state"],
                "mock_publication_calls": result.vertical_slice.publisher.calls,
                "real_external_writes": result.external_writes,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
