from __future__ import annotations

import copy
import multiprocessing
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.capabilities import (
    CapabilityError,
    CapabilityInactiveError,
    CapabilityRegistry,
    SQLiteCapabilityRegistry,
)
from agency_os.contracts import (
    ContractError,
    finalize_record,
    make_capability_record,
)
from agency_os.store import Principal


def _suspend_capability_in_process(database_path: str) -> None:
    registry = SQLiteCapabilityRegistry(database_path)
    director = Principal("agent_director", "agency-director", "brand_lantern")
    registry.suspend(director, "brand_lantern", "cap_publish")


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


class SQLiteCapabilityRegistryTests(CapabilityRegistryTests):
    def setUp(self) -> None:
        super().setUp()
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.database_path = Path(temporary_directory.name) / "capabilities.sqlite3"
        self.registry = SQLiteCapabilityRegistry(self.database_path)

    def test_grant_and_suspension_survive_registry_restart(self) -> None:
        self.registry.register(self.director, self.capability)
        restarted = SQLiteCapabilityRegistry(self.database_path)
        resolved, status = restarted.resolve("brand_lantern", "cap_publish")
        self.assertEqual(resolved, self.capability)
        self.assertEqual(status, "active")

        restarted.suspend(self.director, "brand_lantern", "cap_publish")
        second_restart = SQLiteCapabilityRegistry(self.database_path)
        _, status = second_restart.resolve("brand_lantern", "cap_publish")
        self.assertEqual(status, "suspended")

    def test_suspension_is_shared_with_another_process(self) -> None:
        self.registry.register(self.director, self.capability)
        process = multiprocessing.get_context("spawn").Process(
            target=_suspend_capability_in_process,
            args=(str(self.database_path),),
        )
        process.start()
        process.join(timeout=5)
        self.assertEqual(process.exitcode, 0)
        _, status = self.registry.resolve("brand_lantern", "cap_publish")
        self.assertEqual(status, "suspended")

    def test_database_file_is_restricted_to_owner(self) -> None:
        mode = stat.S_IMODE(self.database_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_group_writable_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            parent.chmod(0o770)

            with self.assertRaises(CapabilityError):
                SQLiteCapabilityRegistry(parent / "capabilities.sqlite3")

    def test_running_registry_rejects_database_identity_replacement(self) -> None:
        self.registry.register(self.director, self.capability)
        replacement_path = self.database_path.with_name("replacement.sqlite3")
        replacement = SQLiteCapabilityRegistry(replacement_path)
        replacement.register(self.director, self.capability)
        replacement_path.replace(self.database_path)

        with self.assertRaises(CapabilityError):
            self.registry.resolve("brand_lantern", "cap_publish")

    def test_dispatch_and_suspension_are_ordered_across_instances(self) -> None:
        self.registry.register(self.director, self.capability)
        other = SQLiteCapabilityRegistry(self.database_path, timeout_seconds=2)
        entered = threading.Event()
        release = threading.Event()
        suspend_started = threading.Event()
        calls = 0

        def dispatch(authorization_guard):
            nonlocal calls
            authorization_guard.acquire()
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release durable dispatch")
            calls += 1
            return "published"

        def suspend():
            suspend_started.set()
            other.suspend(self.director, "brand_lantern", "cap_publish")

        with ThreadPoolExecutor(max_workers=2) as pool:
            dispatch_future = pool.submit(
                self.registry.authorized_dispatch,
                "brand_lantern",
                "cap_publish",
                self.capability["content_checksum"],
                clock=lambda: self.now,
                pre_dispatch=lambda _now: None,
                dispatch=dispatch,
            )
            self.assertTrue(entered.wait(timeout=1))
            suspend_future = pool.submit(suspend)
            self.assertTrue(suspend_started.wait(timeout=1))
            self.assertFalse(suspend_future.done())
            release.set()
            self.assertEqual(dispatch_future.result(timeout=1), "published")
            suspend_future.result(timeout=1)

        self.assertEqual(calls, 1)
        _, status = self.registry.resolve("brand_lantern", "cap_publish")
        self.assertEqual(status, "suspended")
        with self.assertRaises(CapabilityInactiveError):
            self.registry.authorized_dispatch(
                "brand_lantern",
                "cap_publish",
                self.capability["content_checksum"],
                clock=lambda: self.now,
                pre_dispatch=lambda _now: None,
                dispatch=lambda _authorization_guard: "unexpected",
            )

    def test_suspension_before_final_authorization_wins_across_instances(self) -> None:
        self.registry.register(self.director, self.capability)
        other = SQLiteCapabilityRegistry(self.database_path, timeout_seconds=2)
        adapter_entered = threading.Event()
        continue_to_credential = threading.Event()
        calls = 0

        def dispatch(authorization_guard):
            nonlocal calls
            adapter_entered.set()
            if not continue_to_credential.wait(timeout=2):
                raise TimeoutError("test did not continue to credential")
            authorization_guard.acquire()
            calls += 1
            return "unexpected"

        with ThreadPoolExecutor(max_workers=1) as pool:
            dispatch_future = pool.submit(
                self.registry.authorized_dispatch,
                "brand_lantern",
                "cap_publish",
                self.capability["content_checksum"],
                clock=lambda: self.now,
                pre_dispatch=lambda _now: None,
                dispatch=dispatch,
            )
            self.assertTrue(adapter_entered.wait(timeout=1))
            other.suspend(self.director, "brand_lantern", "cap_publish")
            continue_to_credential.set()
            with self.assertRaises(CapabilityInactiveError):
                dispatch_future.result(timeout=1)

        self.assertEqual(calls, 0)
        _, status = self.registry.resolve("brand_lantern", "cap_publish")
        self.assertEqual(status, "suspended")


if __name__ == "__main__":
    unittest.main()
