from __future__ import annotations

import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agency_os.ledger import LedgerError, SQLiteActionLedger


def _reserve_in_process(database_path: str, start, results) -> None:
    if not start.wait(timeout=5):
        results.put("START_TIMEOUT")
        return
    try:
        ledger = SQLiteActionLedger(database_path)
        reservation = ledger.reserve(
            "brand_lantern", "process-shared", "sha256:request"
        )
        results.put(reservation.status)
    except Exception as exc:
        results.put(f"ERROR:{type(exc).__name__}:{exc}")


class LedgerTests(unittest.TestCase):
    def test_durable_ledger_rejects_memory_only_database(self) -> None:
        with self.assertRaises(ValueError):
            SQLiteActionLedger(":memory:")

    def test_initialization_retries_a_transient_database_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "action-ledger.sqlite3"
            initialize_once = SQLiteActionLedger._initialize_once
            attempts = 0

            def transient_lock(ledger) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    try:
                        raise sqlite3.OperationalError("database is locked")
                    except sqlite3.OperationalError as exc:
                        raise LedgerError(
                            "could not initialize action ledger"
                        ) from exc
                initialize_once(ledger)

            with patch.object(
                SQLiteActionLedger, "_initialize_once", transient_lock
            ):
                SQLiteActionLedger(database_path, timeout_seconds=0.5)

        self.assertEqual(attempts, 2)

    def test_unsafe_existing_database_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "action-ledger.sqlite3"
            database_path.touch(mode=0o644)
            database_path.chmod(0o644)

            with self.assertRaises(LedgerError):
                SQLiteActionLedger(database_path)

    def test_group_writable_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            parent.chmod(0o770)

            with self.assertRaises(LedgerError):
                SQLiteActionLedger(parent / "action-ledger.sqlite3")

    def test_symlink_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            real_parent = root / "real"
            real_parent.mkdir(mode=0o700)
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            with self.assertRaises(LedgerError):
                SQLiteActionLedger(linked_parent / "action-ledger.sqlite3")

    def test_running_ledger_rejects_database_identity_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            database_path = parent / "action-ledger.sqlite3"
            replacement_path = parent / "replacement.sqlite3"
            ledger = SQLiteActionLedger(database_path)
            SQLiteActionLedger(replacement_path)
            replacement_path.replace(database_path)

            with self.assertRaises(LedgerError):
                ledger.reserve("brand_lantern", "replaced", "sha256:request")

    def test_separate_processes_share_one_atomic_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "action-ledger.sqlite3"
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=_reserve_in_process,
                    args=(str(database_path), start, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            statuses = sorted(results.get(timeout=10) for _ in processes)
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)

        self.assertEqual(statuses, ["BLOCKED", "RESERVED"])

    def test_identical_keys_are_independent_between_brands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "action-ledger.sqlite3"
            ledger = SQLiteActionLedger(database_path)

            brand_a = ledger.reserve(
                "brand_a", "same-client-key", "sha256:brand-a-request"
            )
            brand_b = ledger.reserve(
                "brand_b", "same-client-key", "sha256:brand-b-request"
            )
            brand_a_rebound = ledger.reserve(
                "brand_a", "same-client-key", "sha256:changed-request"
            )

        self.assertEqual(brand_a.status, "RESERVED")
        self.assertEqual(brand_b.status, "RESERVED")
        self.assertEqual(brand_a_rebound.status, "CONFLICT")


if __name__ == "__main__":
    unittest.main()
