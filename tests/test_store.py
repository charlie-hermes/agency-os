from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agency_os.contracts import ContractError, finalize_record
from agency_os.store import AuthorizationError, Principal, TenantStore


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TenantStore()
        self.director_a = Principal("director_a", "agency-director", "brand_a")
        self.director_b = Principal("director_b", "agency-director", "brand_b")

    def _learning(
        self,
        record_id: str,
        *,
        validation: str = "validated",
        lifecycle: str = "active",
        fresh_delta: timedelta = timedelta(days=1),
    ) -> dict:
        now = datetime.now(timezone.utc)
        return finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "learning_record",
                "learning_record_id": record_id,
                "version": 1,
                "brand_id": "brand_a",
                "validation_status": validation,
                "lifecycle_status": lifecycle,
                "reuse_scope": "brand-only",
                "expected_result": "first attempt succeeds",
                "actual_result": "first attempt failed",
                "attempted_approach": "approach-a",
                "validated_correction": "approach-b",
                "evidence_refs": ["evidence_1"],
                "confidence": 0.9,
                "limitations": [],
                "fresh_until": (now + fresh_delta).isoformat(),
                "reviewed_at": now.isoformat(),
                "supersedes": None,
                "dispositioned_by": "director_a",
            }
        )

    def test_cross_tenant_read_is_indistinguishable_from_absence(self) -> None:
        self.store.put(self.director_a, self._learning("learn_a"))
        with self.assertRaises(KeyError):
            self.store.get(self.director_b, "learn_a")

    def test_cross_tenant_and_wrong_role_writes_are_denied(self) -> None:
        record = self._learning("learn_a")
        with self.assertRaises(AuthorizationError):
            self.store.put(self.director_b, record)
        producer = Principal("producer_a", "content-producer", "brand_a")
        with self.assertRaises(AuthorizationError):
            self.store.put(producer, record)

    def test_learning_query_filters_unsafe_records(self) -> None:
        records = [
            self._learning("active"),
            self._learning("expired", fresh_delta=timedelta(seconds=-1)),
            self._learning("candidate", validation="candidate"),
            self._learning("superseded", lifecycle="superseded"),
        ]
        for record in records:
            self.store.put(self.director_a, record)
        result = self.store.active_learning(self.director_a)
        self.assertEqual([record["learning_record_id"] for record in result], ["active"])

    def test_snapshot_restore_is_tenant_bound(self) -> None:
        self.store.put(self.director_a, self._learning("learn_a"))
        snapshot = self.store.snapshot(self.director_a)
        restored = TenantStore.restore(self.director_a, snapshot)
        self.assertEqual(
            restored.get(self.director_a, "learn_a")["learning_record_id"], "learn_a"
        )
        with self.assertRaises(AuthorizationError):
            TenantStore.restore(self.director_b, snapshot)

    def test_conflicting_record_replacement_is_denied(self) -> None:
        original = self._learning("learn_a")
        self.store.put(self.director_a, original)
        changed = dict(original)
        changed["version"] = 2
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            self.store.put(self.director_a, changed)
        self.assertEqual(self.store.get(self.director_a, "learn_a"), original)

    def test_non_director_cannot_snapshot_or_read_learning(self) -> None:
        self.store.put(self.director_a, self._learning("learn_a"))
        publisher = Principal("publisher_a", "publishing-operator", "brand_a")
        with self.assertRaises(AuthorizationError):
            self.store.get(publisher, "learn_a")
        with self.assertRaises(AuthorizationError):
            self.store.snapshot(publisher)


if __name__ == "__main__":
    unittest.main()
