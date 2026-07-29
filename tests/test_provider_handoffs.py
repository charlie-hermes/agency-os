from __future__ import annotations

import unittest

from agency_os.contracts import verify_record
from agency_os.provider_handoffs import (
    ProviderHandoffError,
    create_manual_handoff,
    load_provider_catalog,
)


class ProviderHandoffTests(unittest.TestCase):
    def test_catalog_covers_required_service_classes_without_claiming_connections(self) -> None:
        catalog = load_provider_catalog()
        self.assertEqual(
            {item["service_class"] for item in catalog["providers"]},
            {"cms", "analytics", "search_console", "seo_data", "social", "creative", "crm"},
        )
        self.assertEqual({item["mode"] for item in catalog["providers"]}, {"manual_handoff"})
        self.assertEqual({item["status"] for item in catalog["providers"]}, {"available"})
        self.assertEqual({item["external_write"] for item in catalog["providers"]}, {False})

    def test_manual_handoff_is_checksum_bound_and_does_not_claim_success(self) -> None:
        handoff = create_manual_handoff(
            catalog=load_provider_catalog(),
            capability_id="cms.publish",
            brand_id="brand_lantern",
            campaign_id="camp_summer",
            paperclip_issue_id="00000000-0000-4000-8000-000000000107",
            approved_artifact_checksum="sha256:" + "a" * 64,
            destination_ref="cms:owner-selected",
        )
        verify_record(handoff)
        self.assertEqual(handoff["status"], "awaiting_operator")
        self.assertFalse(handoff["external_write_performed"])

    def test_unknown_or_connected_capability_cannot_be_faked_as_manual(self) -> None:
        with self.assertRaises(ProviderHandoffError):
            create_manual_handoff(
                catalog=load_provider_catalog(),
                capability_id="unknown.publish",
                brand_id="brand_lantern",
                campaign_id="camp_summer",
                paperclip_issue_id="issue",
                approved_artifact_checksum="sha256:" + "a" * 64,
                destination_ref="unknown:account",
            )


if __name__ == "__main__":
    unittest.main()
