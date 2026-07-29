from __future__ import annotations

import unittest

from agency_os.fictional_platforms import InMemoryPaperclipTransport
from agency_os.integrations import PaperclipBrandBinding, PaperclipLifecycleAdapter
from agency_os.operator_portal import build_operator_views


class OperatorPortalTests(unittest.TestCase):
    def test_all_operator_views_are_read_only_and_multi_brand(self) -> None:
        adapters = []
        transports = []
        for suffix, brand_id in ((1, "brand_lantern"), (2, "brand_orchard")):
            binding = PaperclipBrandBinding(
                f"00000000-0000-4000-8000-{suffix:012d}",
                brand_id,
            )
            transport = InMemoryPaperclipTransport(
                company_id=binding.company_id,
                brand_id=binding.brand_id,
            )
            adapter = PaperclipLifecycleAdapter(transport, binding)
            adapter.create_task(
                title=f"{brand_id} campaign",
                campaign_id=f"camp_{suffix}",
                stage="measurement_learning",
                acceptance_criteria=["visible"],
                status="done",
                idempotency_key=f"portal-{suffix}",
                artifact_refs=[f"performance_{suffix}"],
            )
            unrelated_id = f"00000000-0000-4000-8000-{suffix + 900:012x}"
            transport.issues[unrelated_id] = {
                "id": unrelated_id,
                "companyId": binding.company_id,
                "title": "Unrelated operator task",
                "description": "This task belongs to another Paperclip workflow.",
                "status": "done",
                "blockedByIssueIds": [],
            }
            transports.append(transport)
            adapters.append(adapter)
        before = [len(item.calls) for item in transports]
        views = build_operator_views(adapters)
        after_calls = [item.calls[before[index]:] for index, item in enumerate(transports)]
        self.assertEqual(views["authority"], "paperclip")
        self.assertEqual(views["projection"], "read_only")
        self.assertEqual(views["portfolio"]["brand_count"], 2)
        self.assertEqual(views["portfolio"]["campaign_count"], 2)
        self.assertEqual(views["portfolio"]["task_counts"], {"done": 2})
        self.assertEqual(len(views["brands"]), 2)
        self.assertEqual(len(views["campaigns"]), 2)
        self.assertEqual(len(views["calendar"]), 2)
        self.assertEqual(len(views["performance"]), 2)
        self.assertEqual(views["admin"]["role_bundle_count"], 12)
        self.assertEqual(len(views["admin"]["providers"]), 7)
        self.assertTrue(
            all(
                method == "GET"
                for calls in after_calls
                for method, _path, _body in calls
            )
        )


if __name__ == "__main__":
    unittest.main()
