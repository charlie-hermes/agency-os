"""Run and summarize the complete fictional Core workflow."""

from __future__ import annotations

import json

from .core_workflow import run_core_workflow


def main() -> None:
    result = run_core_workflow()
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
