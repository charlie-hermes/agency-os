from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from agency_os.ledger import SQLiteActionLedger


def _reserve_in_process(database_path: str, start, results) -> None:
    ledger = SQLiteActionLedger(database_path)
    if not start.wait(timeout=5):
        results.put("START_TIMEOUT")
        return
    results.put(ledger.reserve("process-shared", "sha256:request").status)


class LedgerTests(unittest.TestCase):
    def test_durable_ledger_rejects_memory_only_database(self) -> None:
        with self.assertRaises(ValueError):
            SQLiteActionLedger(":memory:")

    def test_database_file_is_restricted_to_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "action-ledger.sqlite3"
            database_path.touch(mode=0o644)
            database_path.chmod(0o644)

            SQLiteActionLedger(database_path)

            self.assertEqual(database_path.stat().st_mode & 0o777, 0o600)

    def test_separate_processes_share_one_atomic_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "action-ledger.sqlite3"
            SQLiteActionLedger(database_path)
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


if __name__ == "__main__":
    unittest.main()
