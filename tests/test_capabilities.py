from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from agency_os.capabilities import CapabilityError, CapabilityRegistry
from agency_os.contracts import (
    ContractError,
    finalize_record,
    make_capability_record,
)
from agency_os.store import Principal


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.director = Principal(
            "agent_director", "agency-director", "brand_lantern"
        )
        self.publisher = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        self.registry = CapabilityRegistry()
        self.capability = make_capability_record(
            capability_id="cap_publish",
            brand_id="brand_lantern",
            actor_id="agent_publisher",
            role_id="publishing-operator",
            destination_ref="mock_cms:lantern",
            environment="sandbox",
            operation="publish",
            action_class="external_write",
            data_class="public_content",
            issued_by="agent_director",
            issued_at=(self.now - timedelta(minutes=5)).isoformat(),
            not_before=(self.now - timedelta(minutes=5)).isoformat(),
            expires_at=(self.now + timedelta(minutes=30)).isoformat(),
        )

    def test_only_authenticated_same_brand_director_can_issue(self) -> None:
        with self.assertRaises(CapabilityError):
            self.registry.register(self.publisher, self.capability)

        foreign_director = Principal(
            "other_director", "agency-director", "brand_other"
        )
        with self.assertRaises(CapabilityError):
            self.registry.register(foreign_director, self.capability)

        forged_issuer = copy.deepcopy(self.capability)
        forged_issuer["issued_by"] = "someone_else"
        forged_issuer = finalize_record(forged_issuer)
        with self.assertRaises(CapabilityError):
            self.registry.register(self.director, forged_issuer)

    def test_grant_is_immutable_and_suspension_is_authoritative(self) -> None:
        self.registry.register(self.director, self.capability)
        self.registry.register(self.director, self.capability)

        replacement = copy.deepcopy(self.capability)
        replacement["destination_ref"] = "mock_cms:other"
        replacement = finalize_record(replacement)
        with self.assertRaises(ContractError):
            self.registry.register(self.director, replacement)

        self.registry.suspend(
            self.director, "brand_lantern", self.capability["capability_id"]
        )
        resolved, status = self.registry.resolve(
            "brand_lantern", self.capability["capability_id"]
        )
        self.assertEqual(resolved, self.capability)
        self.assertEqual(status, "suspended")

    def test_identical_ids_are_scoped_by_brand(self) -> None:
        self.registry.register(self.director, self.capability)
        other_director = Principal(
            "other_director", "agency-director", "brand_other"
        )
        other = copy.deepcopy(self.capability)
        other.update(
            {
                "brand_id": "brand_other",
                "actor_id": "other_publisher",
                "issued_by": "other_director",
            }
        )
        other = finalize_record(other)
        self.registry.register(other_director, other)

        first, _ = self.registry.resolve("brand_lantern", "cap_publish")
        second, _ = self.registry.resolve("brand_other", "cap_publish")
        self.assertEqual(first["actor_id"], "agent_publisher")
        self.assertEqual(second["actor_id"], "other_publisher")


if __name__ == "__main__":
    unittest.main()
