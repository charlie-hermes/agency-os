"""Authoritative, tenant-scoped capability admission for external actions."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from .contracts import (
    ContractError,
    canonical_bytes,
    parse_time,
    require_fields,
    utc_now,
    verify_record,
)
from .store import Principal


class CapabilityError(ValueError):
    """A capability record or lifecycle transition is invalid."""


class CapabilityInactiveError(CapabilityError):
    """The capability is not live at the dispatch boundary."""


class CapabilityNotYetEffectiveError(CapabilityInactiveError):
    """The capability validity window has not started at dispatch."""


class CapabilityExpiredError(CapabilityInactiveError):
    """The capability validity window ended before dispatch."""


class CapabilityDriftError(CapabilityError):
    """The authoritative capability no longer matches the admitted grant."""


class CapabilityDispatchError(RuntimeError):
    """The authorized external dispatch failed or returned an unknown result."""


DispatchResult = TypeVar("DispatchResult")


class CapabilityRegistry:
    """Thread-safe authority for immutable grants and ordered suspension."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._suspended: set[tuple[str, str]] = set()
        self._lock = threading.RLock()
        self._dispatch_lock = threading.RLock()
        self._dispatch_context = threading.local()
        self.audit: list[dict[str, str]] = []

    def register(self, issuer: Principal, record: Mapping[str, Any]) -> str:
        verify_record(record)
        require_fields(
            record,
            (
                "artifact_type",
                "capability_id",
                "brand_id",
                "actor_id",
                "role_id",
                "destination_ref",
                "environment",
                "operation",
                "action_class",
                "data_class",
                "issued_by",
                "issued_at",
                "not_before",
                "expires_at",
                "status",
            ),
        )
        if record["artifact_type"] != "capability_record":
            raise CapabilityError("not a capability record")
        if issuer.role_id != "agency-director":
            self._audit(issuer, str(record["capability_id"]), "DENY_ISSUER_ROLE")
            raise CapabilityError("only the agency director may issue capabilities")
        if record["brand_id"] != issuer.brand_id:
            self._audit(issuer, str(record["capability_id"]), "DENY_TENANT")
            raise CapabilityError("cross-tenant capability issue denied")
        if record["issued_by"] != issuer.actor_id:
            self._audit(issuer, str(record["capability_id"]), "DENY_ISSUER_IDENTITY")
            raise CapabilityError(
                "capability issuer does not match authenticated actor"
            )
        if record["status"] != "active":
            raise CapabilityError("new capability must start active")
        if parse_time(record["issued_at"]) > parse_time(record["not_before"]):
            raise CapabilityError("capability cannot become valid before it is issued")
        if parse_time(record["not_before"]) >= parse_time(record["expires_at"]):
            raise CapabilityError("capability validity window is empty")
        for field in (
            "capability_id",
            "brand_id",
            "actor_id",
            "role_id",
            "destination_ref",
            "environment",
            "operation",
            "action_class",
            "data_class",
        ):
            if not isinstance(record[field], str) or not record[field]:
                raise CapabilityError(f"capability field {field!r} must be non-empty")

        capability_id = record["capability_id"]
        key = (record["brand_id"], capability_id)
        new_record = copy.deepcopy(dict(record))
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing != new_record:
                    self._audit(issuer, capability_id, "DENY_IMMUTABLE_CONFLICT")
                    raise ContractError(f"capability {capability_id!r} is immutable")
                self._audit(issuer, capability_id, "ALLOW_IDEMPOTENT")
                return capability_id
            self._records[key] = new_record
        self._audit(issuer, capability_id, "ALLOW")
        return capability_id

    def suspend(self, issuer: Principal, brand_id: str, capability_id: str) -> None:
        if getattr(self._dispatch_context, "active", False):
            raise CapabilityError(
                "capability suspension cannot run inside authorized dispatch"
            )
        if issuer.role_id != "agency-director":
            self._audit(issuer, capability_id, "DENY_SUSPEND_ROLE")
            raise CapabilityError("only the agency director may suspend capabilities")
        if issuer.brand_id != brand_id:
            self._audit(issuer, capability_id, "DENY_SUSPEND_TENANT")
            raise CapabilityError("cross-tenant capability suspension denied")
        key = (brand_id, capability_id)
        with self._dispatch_lock:
            with self._lock:
                if key not in self._records:
                    self._audit(issuer, capability_id, "DENY_NOT_FOUND")
                    raise KeyError(capability_id)
                self._suspended.add(key)
        self._audit(issuer, capability_id, "SUSPEND")

    def resolve(self, brand_id: str, capability_id: str) -> tuple[dict[str, Any], str]:
        with self._lock:
            return self._resolve_locked(brand_id, capability_id)

    def authorized_dispatch(
        self,
        brand_id: str,
        capability_id: str,
        expected_checksum: str,
        *,
        clock: Callable[[], datetime],
        pre_dispatch: Callable[[datetime], None],
        dispatch: Callable[[], DispatchResult],
    ) -> DispatchResult:
        """Atomically validate live authority and invoke the egress adapter.

        Suspension and dispatch use the same lock. If suspension acquires it
        first, dispatch is denied. If dispatch acquires it first, the authority
        samples its clock, validates the grant window, validates other caller
        windows, and invokes the adapter before suspension can take effect.
        """

        if getattr(self._dispatch_context, "active", False):
            raise CapabilityError("nested authorized dispatch is denied")
        with self._dispatch_lock:
            self._dispatch_context.active = True
            try:
                with self._lock:
                    record, status = self._resolve_locked(brand_id, capability_id)
                    if record.get("status") != "active" or status != "active":
                        raise CapabilityInactiveError("capability is inactive")
                    if record.get("content_checksum") != expected_checksum:
                        raise CapabilityDriftError("capability checksum changed")
                    try:
                        dispatch_time = clock()
                    except Exception as exc:
                        raise CapabilityError("dispatch clock is unavailable") from exc
                    if (
                        not isinstance(dispatch_time, datetime)
                        or dispatch_time.tzinfo is None
                    ):
                        raise CapabilityError(
                            "dispatch clock must be timezone-aware"
                        )
                    if parse_time(record["not_before"]) > dispatch_time:
                        raise CapabilityNotYetEffectiveError(
                            "capability is not yet effective"
                        )
                    if parse_time(record["expires_at"]) <= dispatch_time:
                        raise CapabilityExpiredError("capability is expired")
                pre_dispatch(dispatch_time)
                try:
                    return dispatch()
                except Exception as exc:
                    raise CapabilityDispatchError(
                        "authorized external dispatch failed"
                    ) from exc
            finally:
                self._dispatch_context.active = False

    def _resolve_locked(
        self, brand_id: str, capability_id: str
    ) -> tuple[dict[str, Any], str]:
        key = (brand_id, capability_id)
        record = copy.deepcopy(self._records.get(key))
        status = "suspended" if key in self._suspended else "active"
        if record is None:
            raise KeyError(capability_id)
        verify_record(record)
        if (
            record.get("artifact_type") != "capability_record"
            or record.get("capability_id") != capability_id
            or record.get("brand_id") != brand_id
        ):
            raise CapabilityError("stored capability binding is invalid")
        return record, status

    def _audit(self, principal: Principal, capability_id: str, outcome: str) -> None:
        self.audit.append(
            {
                "actor_id": principal.actor_id,
                "role_id": principal.role_id,
                "brand_id": principal.brand_id,
                "capability_id": capability_id,
                "outcome": outcome,
            }
        )


class SQLiteCapabilityRegistry(CapabilityRegistry):
    """Process-shared capability authority backed by local SQLite storage."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__()
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("SQLiteCapabilityRegistry requires a durable file path")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        if self.database_path.exists():
            self._restrict_permissions()
        self._initialize()

    def register(self, issuer: Principal, record: Mapping[str, Any]) -> str:
        validator = CapabilityRegistry()
        capability_id = validator.register(issuer, record)
        record_copy = copy.deepcopy(dict(record))
        record_json = canonical_bytes(record_copy).decode("utf-8")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT record_json
                FROM capability_registry
                WHERE brand_id = ? AND capability_id = ?
                """,
                (issuer.brand_id, capability_id),
            ).fetchone()
            if row is not None:
                existing = json.loads(row[0])
                if existing != record_copy:
                    raise ContractError(f"capability {capability_id!r} is immutable")
                connection.commit()
                self._audit(issuer, capability_id, "ALLOW_IDEMPOTENT")
                return capability_id
            now = utc_now()
            connection.execute(
                """
                INSERT INTO capability_registry (
                    brand_id, capability_id, record_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (issuer.brand_id, capability_id, record_json, now, now),
            )
            connection.commit()
        except (ContractError, CapabilityError):
            _rollback_capability(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback_capability(connection)
            raise CapabilityError("could not register durable capability") from exc
        finally:
            connection.close()
        self._audit(issuer, capability_id, "ALLOW")
        return capability_id

    def suspend(self, issuer: Principal, brand_id: str, capability_id: str) -> None:
        if getattr(self._dispatch_context, "active", False):
            raise CapabilityError(
                "capability suspension cannot run inside authorized dispatch"
            )
        if issuer.role_id != "agency-director":
            raise CapabilityError("only the agency director may suspend capabilities")
        if issuer.brand_id != brand_id:
            raise CapabilityError("cross-tenant capability suspension denied")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT 1 FROM capability_registry
                WHERE brand_id = ? AND capability_id = ?
                """,
                (brand_id, capability_id),
            ).fetchone()
            if row is None:
                raise KeyError(capability_id)
            connection.execute(
                """
                UPDATE capability_registry
                SET state = 'suspended', updated_at = ?
                WHERE brand_id = ? AND capability_id = ?
                """,
                (utc_now(), brand_id, capability_id),
            )
            connection.commit()
        except (KeyError, CapabilityError):
            _rollback_capability(connection)
            raise
        except sqlite3.Error as exc:
            _rollback_capability(connection)
            raise CapabilityError("could not suspend durable capability") from exc
        finally:
            connection.close()
        self._audit(issuer, capability_id, "SUSPEND")

    def resolve(self, brand_id: str, capability_id: str) -> tuple[dict[str, Any], str]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT record_json, state
                FROM capability_registry
                WHERE brand_id = ? AND capability_id = ?
                """,
                (brand_id, capability_id),
            ).fetchone()
            if row is None:
                raise KeyError(capability_id)
            return _validated_durable_capability(
                brand_id, capability_id, row[0], row[1]
            )
        except (KeyError, CapabilityError, ContractError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CapabilityError("could not resolve durable capability") from exc
        finally:
            connection.close()

    def authorized_dispatch(
        self,
        brand_id: str,
        capability_id: str,
        expected_checksum: str,
        *,
        clock: Callable[[], datetime],
        pre_dispatch: Callable[[datetime], None],
        dispatch: Callable[[], DispatchResult],
    ) -> DispatchResult:
        if getattr(self._dispatch_context, "active", False):
            raise CapabilityError("nested authorized dispatch is denied")
        connection = self._connect()
        dispatched = False
        self._dispatch_context.active = True
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT record_json, state
                FROM capability_registry
                WHERE brand_id = ? AND capability_id = ?
                """,
                (brand_id, capability_id),
            ).fetchone()
            if row is None:
                raise KeyError(capability_id)
            record, status = _validated_durable_capability(
                brand_id, capability_id, row[0], row[1]
            )
            if record.get("status") != "active" or status != "active":
                raise CapabilityInactiveError("capability is inactive")
            if record.get("content_checksum") != expected_checksum:
                raise CapabilityDriftError("capability checksum changed")
            try:
                dispatch_time = clock()
            except Exception as exc:
                raise CapabilityError("dispatch clock is unavailable") from exc
            if not isinstance(dispatch_time, datetime) or dispatch_time.tzinfo is None:
                raise CapabilityError("dispatch clock must be timezone-aware")
            if parse_time(record["not_before"]) > dispatch_time:
                raise CapabilityNotYetEffectiveError("capability is not yet effective")
            if parse_time(record["expires_at"]) <= dispatch_time:
                raise CapabilityExpiredError("capability is expired")
            pre_dispatch(dispatch_time)
            try:
                result = dispatch()
                dispatched = True
            except Exception as exc:
                raise CapabilityDispatchError(
                    "authorized external dispatch failed"
                ) from exc
            try:
                connection.commit()
            except sqlite3.Error as exc:
                raise CapabilityDispatchError(
                    "could not finish authorized dispatch"
                ) from exc
            return result
        except CapabilityDispatchError:
            _rollback_capability(connection)
            raise
        except (KeyError, CapabilityError, ContractError):
            _rollback_capability(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback_capability(connection)
            if dispatched:
                raise CapabilityDispatchError(
                    "authorized external result is unknown"
                ) from exc
            raise CapabilityError("could not authorize durable dispatch") from exc
        finally:
            self._dispatch_context.active = False
            connection.close()

    def _initialize(self) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._initialize_once()
                break
            except CapabilityError as exc:
                if "locked" not in str(exc.__cause__).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
        self._restrict_permissions()

    def _initialize_once(self) -> None:
        connection = self._connect()
        try:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise CapabilityError("capability registry requires SQLite WAL mode")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS capability_registry (
                    brand_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, capability_id)
                )
                """
            )
            connection.commit()
        except CapabilityError:
            _rollback_capability(connection)
            raise
        except sqlite3.Error as exc:
            _rollback_capability(connection)
            raise CapabilityError("could not initialize capability registry") from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            connection.execute(
                f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}"
            )
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.Error as exc:
            raise CapabilityError("could not open capability registry") from exc

    def _restrict_permissions(self) -> None:
        try:
            self.database_path.chmod(0o600)
        except OSError as exc:
            raise CapabilityError("could not restrict capability registry") from exc


def _validated_durable_capability(
    brand_id: str, capability_id: str, record_json: str, state: str
) -> tuple[dict[str, Any], str]:
    record = json.loads(record_json)
    if not isinstance(record, dict):
        raise CapabilityError("stored capability is not an object")
    verify_record(record)
    if (
        record.get("artifact_type") != "capability_record"
        or record.get("brand_id") != brand_id
        or record.get("capability_id") != capability_id
        or state not in {"active", "suspended"}
    ):
        raise CapabilityError("stored capability binding is invalid")
    return record, state


def _rollback_capability(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass
