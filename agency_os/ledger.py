"""Durable idempotency reservations for external actions."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from .contracts import ContractError, canonical_bytes, utc_now
from .sqlite_storage import (
    SQLiteStorageError,
    prepare_sqlite_storage,
    validate_sqlite_storage,
)


class LedgerError(RuntimeError):
    """The action ledger could not preserve or resolve a safe state."""


ReservationStatus = Literal["RESERVED", "REPLAY", "BLOCKED", "CONFLICT"]


@dataclass(frozen=True)
class Reservation:
    status: ReservationStatus
    receipt: dict[str, Any] | None = None


class ActionLedger(Protocol):
    def reserve(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> Reservation:
        """Atomically reserve a brand-scoped key or resolve its existing state."""

    def complete(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
        receipt: Mapping[str, Any],
    ) -> None:
        """Atomically persist a successful receipt for a reserved action."""

    def mark_unknown(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> None:
        """Persist that an external result requires reconciliation."""


class InMemoryActionLedger:
    """Thread-safe ledger for the fictional single-process demonstration."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def reserve(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> Reservation:
        ledger_key = (brand_id, idempotency_key)
        with self._lock:
            existing = self._entries.get(ledger_key)
            if existing is None:
                self._entries[ledger_key] = {
                    "request_checksum": request_checksum,
                    "state": "REQUESTED",
                    "receipt": None,
                }
                return Reservation("RESERVED")
            if existing["request_checksum"] != request_checksum:
                return Reservation("CONFLICT")
            if existing["state"] in {"REQUESTED", "UNKNOWN"}:
                return Reservation("BLOCKED")
            if existing["state"] == "PUBLISHED" and existing["receipt"] is not None:
                return Reservation("REPLAY", copy.deepcopy(existing["receipt"]))
            raise LedgerError("invalid in-memory ledger state")

    def complete(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
        receipt: Mapping[str, Any],
    ) -> None:
        receipt_copy = _validated_receipt(
            brand_id, idempotency_key, request_checksum, receipt
        )
        with self._lock:
            existing = self._matching_entry(
                brand_id, idempotency_key, request_checksum
            )
            if existing["state"] != "REQUESTED":
                raise LedgerError("only a requested action can be completed")
            existing.update({"state": "PUBLISHED", "receipt": receipt_copy})

    def mark_unknown(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> None:
        with self._lock:
            existing = self._matching_entry(
                brand_id, idempotency_key, request_checksum
            )
            if existing["state"] == "PUBLISHED":
                raise LedgerError("a published action cannot become unknown")
            existing.update({"state": "UNKNOWN", "receipt": None})

    def _matching_entry(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> dict[str, Any]:
        existing = self._entries.get((brand_id, idempotency_key))
        if existing is None:
            raise LedgerError("action reservation is missing")
        if existing["request_checksum"] != request_checksum:
            raise LedgerError("action reservation checksum mismatch")
        return existing


class SQLiteActionLedger:
    """Process-shared durable ledger backed by one local SQLite database."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("SQLiteActionLedger requires a durable file path")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        try:
            self._storage_identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise LedgerError("unsafe action ledger storage") from exc
        self._initialize()

    def reserve(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> Reservation:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_checksum, state, receipt_json
                FROM action_ledger
                WHERE brand_id = ? AND idempotency_key = ?
                """,
                (brand_id, idempotency_key),
            ).fetchone()
            if row is None:
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO action_ledger (
                        brand_id,
                        idempotency_key,
                        request_checksum,
                        state,
                        receipt_json,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, 'REQUESTED', NULL, ?, ?)
                    """,
                    (brand_id, idempotency_key, request_checksum, now, now),
                )
                connection.commit()
                return Reservation("RESERVED")

            stored_checksum, state, receipt_json = row
            if stored_checksum != request_checksum:
                connection.commit()
                return Reservation("CONFLICT")
            if state in {"REQUESTED", "UNKNOWN"}:
                connection.commit()
                return Reservation("BLOCKED")
            if state != "PUBLISHED" or receipt_json is None:
                raise LedgerError("invalid durable ledger state")
            receipt = json.loads(receipt_json)
            if not isinstance(receipt, dict):
                raise LedgerError("stored receipt is not an object")
            connection.commit()
            return Reservation("REPLAY", receipt)
        except LedgerError:
            _rollback(connection)
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise LedgerError("could not reserve action") from exc
        finally:
            connection.close()

    def complete(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
        receipt: Mapping[str, Any],
    ) -> None:
        receipt_copy = _validated_receipt(
            brand_id, idempotency_key, request_checksum, receipt
        )
        receipt_json = canonical_bytes(receipt_copy).decode("utf-8")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stored_checksum, state = self._read_state(
                connection, brand_id, idempotency_key
            )
            if stored_checksum != request_checksum:
                raise LedgerError("action reservation checksum mismatch")
            if state != "REQUESTED":
                raise LedgerError("only a requested action can be completed")
            connection.execute(
                """
                UPDATE action_ledger
                SET state = 'PUBLISHED', receipt_json = ?, updated_at = ?
                WHERE brand_id = ? AND idempotency_key = ?
                """,
                (receipt_json, utc_now(), brand_id, idempotency_key),
            )
            connection.commit()
        except LedgerError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise LedgerError("could not complete action") from exc
        finally:
            connection.close()

    def mark_unknown(
        self,
        brand_id: str,
        idempotency_key: str,
        request_checksum: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stored_checksum, state = self._read_state(
                connection, brand_id, idempotency_key
            )
            if stored_checksum != request_checksum:
                raise LedgerError("action reservation checksum mismatch")
            if state == "PUBLISHED":
                raise LedgerError("a published action cannot become unknown")
            connection.execute(
                """
                UPDATE action_ledger
                SET state = 'UNKNOWN', receipt_json = NULL, updated_at = ?
                WHERE brand_id = ? AND idempotency_key = ?
                """,
                (utc_now(), brand_id, idempotency_key),
            )
            connection.commit()
        except LedgerError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise LedgerError("could not mark action unknown") from exc
        finally:
            connection.close()

    def _initialize(self) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._initialize_once()
                break
            except LedgerError as exc:
                if not _is_locked_error(exc) or time.monotonic() >= deadline:
                    raise
                remaining = deadline - time.monotonic()
                time.sleep(min(0.05, max(remaining, 0.0)))

    def _initialize_once(self) -> None:
        connection = self._connect()
        try:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise LedgerError("action ledger requires SQLite WAL mode")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_ledger (
                    brand_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_checksum TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK (state IN ('REQUESTED', 'UNKNOWN', 'PUBLISHED')),
                    receipt_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (
                        (state = 'PUBLISHED' AND receipt_json IS NOT NULL)
                        OR
                        (state != 'PUBLISHED' AND receipt_json IS NULL)
                    ),
                    PRIMARY KEY (brand_id, idempotency_key)
                )
                """
            )
            connection.commit()
        except LedgerError:
            raise
        except sqlite3.Error as exc:
            raise LedgerError("could not initialize action ledger") from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            validate_sqlite_storage(self.database_path, self._storage_identity)
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            validate_sqlite_storage(self.database_path, self._storage_identity)
            connection.execute(
                f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}"
            )
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise LedgerError("could not open action ledger") from exc

    @staticmethod
    def _read_state(
        connection: sqlite3.Connection,
        brand_id: str,
        idempotency_key: str,
    ) -> tuple[str, str]:
        row = connection.execute(
            """
            SELECT request_checksum, state
            FROM action_ledger
            WHERE brand_id = ? AND idempotency_key = ?
            """,
            (brand_id, idempotency_key),
        ).fetchone()
        if row is None:
            raise LedgerError("action reservation is missing")
        return str(row[0]), str(row[1])


def _validated_receipt(
    brand_id: str,
    idempotency_key: str,
    request_checksum: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    receipt_copy = copy.deepcopy(dict(receipt))
    if receipt_copy.get("brand_id") != brand_id:
        raise LedgerError("receipt brand mismatch")
    if receipt_copy.get("idempotency_key") != idempotency_key:
        raise LedgerError("receipt idempotency key mismatch")
    if receipt_copy.get("request_binding_checksum") != request_checksum:
        raise LedgerError("receipt request checksum mismatch")
    if receipt_copy.get("state") != "PUBLISHED":
        raise LedgerError("only a published receipt can complete an action")
    try:
        canonical_bytes(receipt_copy)
    except ContractError as exc:
        raise LedgerError("receipt is not canonical JSON") from exc
    return receipt_copy


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass


def _is_locked_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, sqlite3.OperationalError):
            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                return True
            message = str(current).lower()
            if "locked" in message or "busy" in message:
                return True
        current = current.__cause__
    return False
