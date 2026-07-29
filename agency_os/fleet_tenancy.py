"""Durable Generation 2 tenant, hostname, and product entitlement authority.

Paperclip remains the task and approval authority.  This authority owns only
the exact bindings needed to route Fleet product data safely between brands.
All records are immutable and checksummed; operational suspension is separate.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from .contracts import (
    ContractError,
    canonical_bytes,
    finalize_record,
    parse_time,
    require_fields,
    utc_now,
    verify_record,
)
from .sqlite_storage import (
    SQLiteStorageError,
    prepare_sqlite_storage,
    validate_sqlite_storage,
)
from .store import Principal


class FleetTenancyError(RuntimeError):
    """The Fleet tenant authority denied or could not complete an operation."""


class FleetTenancyAuthorizationError(PermissionError):
    """A principal is outside the required brand or role boundary."""


PRODUCT_MODULES = frozenset(
    {
        "content_engine",
        "brand_twin",
        "ai_market_observatory",
        "brand_agent",
        "controlled_actions",
        "client_portal",
        "measurement",
        "agentic_commerce",
    }
)

_BRAND_ID = re.compile(r"^brand_[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RESERVED_SLUGS = frozenset({"admin", "api", "app", "auth", "paperclip", "www"})
_READ_ROLES = frozenset({"agency-director", "platform-assurance-reviewer"})
_SCHEMA_VERSION = 1


def make_brand_tenant(
    *,
    tenant_id: str,
    brand_id: str,
    paperclip_company_id: str,
    company_name: str,
    created_by: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build an immutable one-brand-to-one-Paperclip-company binding."""

    record = {
        "schema_version": "2.0",
        "artifact_type": "brand_tenant",
        "tenant_id": tenant_id,
        "brand_id": brand_id,
        "paperclip_company_id": paperclip_company_id,
        "company_name": company_name,
        "status": "active",
        "created_by": created_by,
        "created_at": created_at or utc_now(),
    }
    _validate_brand_tenant_fields(record)
    return finalize_record(record)


def make_portal_hostname_binding(
    *,
    binding_id: str,
    brand_id: str,
    brand_slug: str,
    hostname: str,
    created_by: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build an immutable approved hostname binding for a Fleet brand."""

    record = {
        "schema_version": "2.0",
        "artifact_type": "portal_hostname_binding",
        "binding_id": binding_id,
        "brand_id": brand_id,
        "brand_slug": brand_slug,
        "hostname": _normalise_hostname(hostname),
        "base_domain": "madebyfleet.com",
        "status": "active",
        "created_by": created_by,
        "created_at": created_at or utc_now(),
    }
    _validate_portal_hostname_fields(record)
    return finalize_record(record)


def make_product_entitlement(
    *,
    entitlement_id: str,
    brand_id: str,
    module: str,
    issued_by: str,
    limits: Mapping[str, Any] | None = None,
    issued_at: str | None = None,
) -> dict[str, Any]:
    """Build an immutable module entitlement; absent entitlements are disabled."""

    record = {
        "schema_version": "2.0",
        "artifact_type": "product_entitlement",
        "entitlement_id": entitlement_id,
        "brand_id": brand_id,
        "module": module,
        "limits": copy.deepcopy(dict(limits or {})),
        "status": "active",
        "issued_by": issued_by,
        "issued_at": issued_at or utc_now(),
    }
    _validate_product_entitlement_fields(record)
    return finalize_record(record)


class FleetTenantAuthority:
    """Process-shared, fail-closed authority backed by protected SQLite."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("FleetTenantAuthority requires a durable file path")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        try:
            self._storage_identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise FleetTenancyError("unsafe Fleet tenant authority storage") from exc
        self._initialize()

    def register_tenant(self, principal: Principal, record: Mapping[str, Any]) -> str:
        _require_director(principal)
        _validate_brand_tenant(record)
        if record["brand_id"] != principal.brand_id:
            self._audit(principal, "register_tenant", str(record.get("tenant_id")), "DENY_TENANT")
            raise FleetTenancyAuthorizationError("cross-tenant registration denied")
        if record["created_by"] != principal.actor_id:
            raise FleetTenancyAuthorizationError("tenant creator does not match principal")
        return self._insert_immutable(
            principal,
            operation="register_tenant",
            table="brand_tenants",
            identity_columns=("brand_id", "tenant_id", "paperclip_company_id"),
            identity_values=(record["brand_id"], record["tenant_id"], record["paperclip_company_id"]),
            record=record,
            target_id=record["tenant_id"],
        )

    def register_hostname(self, principal: Principal, record: Mapping[str, Any]) -> str:
        _require_director(principal)
        _validate_portal_hostname(record)
        if record["brand_id"] != principal.brand_id:
            raise FleetTenancyAuthorizationError("cross-tenant hostname registration denied")
        if record["created_by"] != principal.actor_id:
            raise FleetTenancyAuthorizationError("hostname creator does not match principal")
        self._require_active_tenant(principal.brand_id)
        return self._insert_immutable(
            principal,
            operation="register_hostname",
            table="portal_hostnames",
            identity_columns=("hostname", "brand_id", "binding_id"),
            identity_values=(record["hostname"], record["brand_id"], record["binding_id"]),
            record=record,
            target_id=record["binding_id"],
        )

    def grant_entitlement(self, principal: Principal, record: Mapping[str, Any]) -> str:
        _require_director(principal)
        _validate_product_entitlement(record)
        if record["brand_id"] != principal.brand_id:
            raise FleetTenancyAuthorizationError("cross-tenant entitlement denied")
        if record["issued_by"] != principal.actor_id:
            raise FleetTenancyAuthorizationError("entitlement issuer does not match principal")
        self._require_active_tenant(principal.brand_id)
        return self._insert_immutable(
            principal,
            operation="grant_entitlement",
            table="product_entitlements",
            identity_columns=("brand_id", "module", "entitlement_id"),
            identity_values=(record["brand_id"], record["module"], record["entitlement_id"]),
            record=record,
            target_id=record["entitlement_id"],
        )

    def suspend_tenant(self, principal: Principal, brand_id: str) -> None:
        _require_same_brand_director(principal, brand_id)
        self._suspend(principal, "brand_tenants", "brand_id", brand_id, "suspend_tenant")

    def suspend_hostname(self, principal: Principal, hostname: str) -> None:
        _require_director(principal)
        normalised = _normalise_hostname(hostname)
        self._suspend(
            principal,
            "portal_hostnames",
            "hostname",
            normalised,
            "suspend_hostname",
            brand_column="brand_id",
        )

    def suspend_entitlement(self, principal: Principal, module: str) -> None:
        _require_director(principal)
        if module not in PRODUCT_MODULES:
            raise ContractError("unknown product module")
        self._suspend(
            principal,
            "product_entitlements",
            "module",
            module,
            "suspend_entitlement",
            brand_column="brand_id",
        )

    def get_tenant(self, principal: Principal) -> dict[str, Any]:
        _require_reader(principal)
        row = self._fetch_one(
            "SELECT record_json, state FROM brand_tenants WHERE brand_id = ?",
            (principal.brand_id,),
        )
        if row is None:
            self._audit(principal, "get_tenant", principal.brand_id, "NOT_FOUND_OR_WRONG_TENANT")
            raise KeyError(principal.brand_id)
        record = _validated_stored_record(row[0], "brand_tenant", principal.brand_id)
        self._audit(principal, "get_tenant", record["tenant_id"], "ALLOW")
        return record

    def authorize_hostname(self, principal: Principal, hostname: str) -> dict[str, Any]:
        """Resolve an exact host and prove it belongs to the authenticated brand."""

        _require_reader(principal)
        normalised = _normalise_hostname(hostname)
        row = self._fetch_one(
            """
            SELECT h.record_json, h.state, t.state
            FROM portal_hostnames h
            JOIN brand_tenants t ON t.brand_id = h.brand_id
            WHERE h.hostname = ? AND h.brand_id = ?
            """,
            (normalised, principal.brand_id),
        )
        if row is None or row[1] != "active" or row[2] != "active":
            self._audit(principal, "authorize_hostname", normalised, "NOT_FOUND_OR_WRONG_TENANT")
            raise KeyError(normalised)
        record = _validated_stored_record(row[0], "portal_hostname_binding", principal.brand_id)
        if record.get("hostname") != normalised:
            raise FleetTenancyError("stored hostname key does not match its record")
        self._audit(principal, "authorize_hostname", normalised, "ALLOW")
        return record

    def module_enabled(self, principal: Principal, module: str) -> bool:
        """Return false for unknown, absent, suspended, or foreign entitlements."""

        _require_reader(principal)
        if module not in PRODUCT_MODULES:
            return False
        row = self._fetch_one(
            """
            SELECT e.record_json, e.state, t.state
            FROM product_entitlements e
            JOIN brand_tenants t ON t.brand_id = e.brand_id
            WHERE e.brand_id = ? AND e.module = ?
            """,
            (principal.brand_id, module),
        )
        if row is None or row[1] != "active" or row[2] != "active":
            self._audit(principal, "module_enabled", module, "DISABLED")
            return False
        record = _validated_stored_record(row[0], "product_entitlement", principal.brand_id)
        if record.get("module") != module:
            raise FleetTenancyError("stored entitlement key does not match its record")
        self._audit(principal, "module_enabled", module, "ENABLED")
        return True

    def portal_read_model(self, principal: Principal, hostname: str) -> dict[str, Any]:
        """Return the safe server-built routing projection for the future portal."""

        binding = self.authorize_hostname(principal, hostname)
        tenant = self.get_tenant(principal)
        modules = {module: self.module_enabled(principal, module) for module in sorted(PRODUCT_MODULES)}
        return {
            "schema_version": "2.0",
            "brand_id": principal.brand_id,
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "paperclip_company_id": tenant["paperclip_company_id"],
            "hostname": binding["hostname"],
            "brand_slug": binding["brand_slug"],
            "modules": modules,
        }

    def audit_events(self, principal: Principal) -> list[dict[str, Any]]:
        _require_reader(principal)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT sequence, actor_id, role_id, brand_id, operation,
                       target_id, outcome, recorded_at
                FROM authority_audit WHERE brand_id = ? ORDER BY sequence
                """,
                (principal.brand_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise FleetTenancyError("could not read tenant authority audit") from exc
        finally:
            connection.close()
        return [
            {
                "sequence": row[0],
                "actor_id": row[1],
                "role_id": row[2],
                "brand_id": row[3],
                "operation": row[4],
                "target_id": row[5],
                "outcome": row[6],
                "recorded_at": row[7],
            }
            for row in rows
        ]

    def schema_version(self) -> int:
        row = self._fetch_one("SELECT version FROM schema_metadata WHERE id = 1", ())
        if row is None:
            raise FleetTenancyError("tenant authority has no schema metadata")
        return int(row[0])

    def _insert_immutable(
        self,
        principal: Principal,
        *,
        operation: str,
        table: str,
        identity_columns: tuple[str, str, str],
        identity_values: tuple[str, str, str],
        record: Mapping[str, Any],
        target_id: str,
    ) -> str:
        allowed = {
            "brand_tenants": ("brand_id", "tenant_id", "paperclip_company_id"),
            "portal_hostnames": ("hostname", "brand_id", "binding_id"),
            "product_entitlements": ("brand_id", "module", "entitlement_id"),
        }
        if allowed.get(table) != identity_columns:
            raise FleetTenancyError("invalid immutable table binding")
        record_json = canonical_bytes(dict(record)).decode("utf-8")
        lookup_count = 2 if table == "product_entitlements" else 1
        lookup_columns = identity_columns[:lookup_count]
        lookup_values = identity_values[:lookup_count]
        lookup_where = " AND ".join(f"{column} = ?" for column in lookup_columns)
        connection = self._connect()
        outcome = "ALLOW"
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT record_json FROM {table} WHERE {lookup_where}",
                lookup_values,
            ).fetchone()
            if existing is not None:
                if json.loads(existing[0]) != dict(record):
                    raise ContractError(f"{target_id!r} is immutable")
                outcome = "ALLOW_IDEMPOTENT"
            else:
                placeholders = ", ".join("?" for _ in range(7))
                columns = ", ".join((*identity_columns, "record_json", "state", "created_at", "updated_at"))
                now = utc_now()
                connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    (*identity_values, record_json, "active", now, now),
                )
            self._insert_audit(connection, principal, operation, target_id, outcome)
            connection.commit()
        except ContractError:
            _rollback(connection)
            self._audit(principal, operation, target_id, "DENY_IMMUTABLE_CONFLICT")
            raise
        except sqlite3.IntegrityError as exc:
            _rollback(connection)
            self._audit(principal, operation, target_id, "DENY_IMMUTABLE_CONFLICT")
            raise ContractError(
                f"{target_id!r} conflicts with an immutable binding"
            ) from exc
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise FleetTenancyError(f"could not {operation}") from exc
        finally:
            connection.close()
        return target_id

    def _suspend(
        self,
        principal: Principal,
        table: str,
        key_column: str,
        key_value: str,
        operation: str,
        *,
        brand_column: str | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            where = f"{key_column} = ?"
            parameters: tuple[str, ...] = (key_value,)
            if brand_column is not None:
                where += f" AND {brand_column} = ?"
                parameters += (principal.brand_id,)
            row = connection.execute(f"SELECT 1 FROM {table} WHERE {where}", parameters).fetchone()
            if row is None:
                raise KeyError(key_value)
            connection.execute(
                f"UPDATE {table} SET state = 'suspended', updated_at = ? WHERE {where}",
                (utc_now(), *parameters),
            )
            self._insert_audit(connection, principal, operation, key_value, "SUSPEND")
            connection.commit()
        except KeyError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise FleetTenancyError(f"could not {operation}") from exc
        finally:
            connection.close()

    def _require_active_tenant(self, brand_id: str) -> None:
        row = self._fetch_one("SELECT state FROM brand_tenants WHERE brand_id = ?", (brand_id,))
        if row is None or row[0] != "active":
            raise FleetTenancyAuthorizationError("an active tenant binding is required")

    def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> tuple[Any, ...] | None:
        connection = self._connect()
        try:
            return connection.execute(query, parameters).fetchone()
        except sqlite3.Error as exc:
            raise FleetTenancyError("could not read tenant authority") from exc
        finally:
            connection.close()

    def _audit(self, principal: Principal, operation: str, target_id: str, outcome: str) -> None:
        connection = self._connect()
        try:
            self._insert_audit(connection, principal, operation, target_id, outcome)
            connection.commit()
        except sqlite3.Error as exc:
            raise FleetTenancyError("could not write tenant authority audit") from exc
        finally:
            connection.close()

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        principal: Principal,
        operation: str,
        target_id: str,
        outcome: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO authority_audit (
                actor_id, role_id, brand_id, operation, target_id, outcome, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                principal.actor_id,
                principal.role_id,
                principal.brand_id,
                operation,
                target_id,
                outcome,
                utc_now(),
            ),
        )

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
            )
            row = connection.execute("SELECT version FROM schema_metadata WHERE id = 1").fetchone()
            if row is not None and int(row[0]) > _SCHEMA_VERSION:
                raise FleetTenancyError("tenant authority schema is newer than this runtime")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS brand_tenants (
                    brand_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL UNIQUE,
                    paperclip_company_id TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_hostnames (
                    hostname TEXT PRIMARY KEY,
                    brand_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (brand_id) REFERENCES brand_tenants(brand_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS product_entitlements (
                    brand_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    entitlement_id TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, module),
                    FOREIGN KEY (brand_id) REFERENCES brand_tenants(brand_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authority_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO schema_metadata (id, version) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET version = excluded.version",
                (_SCHEMA_VERSION,),
            )
            connection.commit()
        except FleetTenancyError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise FleetTenancyError("could not initialize tenant authority") from exc
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
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise FleetTenancyError("could not open tenant authority") from exc


def _validate_brand_tenant(record: Mapping[str, Any]) -> None:
    verify_record(record)
    _validate_brand_tenant_fields(record)


def _validate_brand_tenant_fields(record: Mapping[str, Any]) -> None:
    require_fields(
        record,
        ("schema_version", "artifact_type", "tenant_id", "brand_id", "paperclip_company_id", "company_name", "status", "created_by", "created_at"),
    )
    if record["schema_version"] != "2.0" or record["artifact_type"] != "brand_tenant":
        raise ContractError("invalid brand tenant contract")
    if not isinstance(record["tenant_id"], str) or not record["tenant_id"].startswith("tenant_"):
        raise ContractError("tenant_id must use the tenant_ prefix")
    _validate_brand_id(record["brand_id"])
    try:
        UUID(str(record["paperclip_company_id"]))
    except ValueError as exc:
        raise ContractError("paperclip_company_id must be a UUID") from exc
    if not isinstance(record["company_name"], str) or not record["company_name"].strip():
        raise ContractError("company_name is required")
    if record["status"] != "active" or not isinstance(record["created_by"], str) or not record["created_by"]:
        raise ContractError("brand tenant must have an active status and creator")
    parse_time(record["created_at"])


def _validate_portal_hostname(record: Mapping[str, Any]) -> None:
    verify_record(record)
    _validate_portal_hostname_fields(record)


def _validate_portal_hostname_fields(record: Mapping[str, Any]) -> None:
    require_fields(record, ("schema_version", "artifact_type", "binding_id", "brand_id", "brand_slug", "hostname", "base_domain", "status", "created_by", "created_at"))
    if record["schema_version"] != "2.0" or record["artifact_type"] != "portal_hostname_binding":
        raise ContractError("invalid portal hostname contract")
    _validate_brand_id(record["brand_id"])
    slug = record["brand_slug"]
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug in _RESERVED_SLUGS:
        raise ContractError("brand_slug is invalid or reserved")
    hostname = _normalise_hostname(record["hostname"])
    if record["base_domain"] != "madebyfleet.com" or hostname != f"{slug}.madebyfleet.com":
        raise ContractError("hostname must exactly match the approved madebyfleet.com brand slug")
    if record["status"] != "active" or not record["binding_id"] or not record["created_by"]:
        raise ContractError("hostname binding must have an id, active status, and creator")
    parse_time(record["created_at"])


def _validate_product_entitlement(record: Mapping[str, Any]) -> None:
    verify_record(record)
    _validate_product_entitlement_fields(record)


def _validate_product_entitlement_fields(record: Mapping[str, Any]) -> None:
    require_fields(record, ("schema_version", "artifact_type", "entitlement_id", "brand_id", "module", "limits", "status", "issued_by", "issued_at"))
    if record["schema_version"] != "2.0" or record["artifact_type"] != "product_entitlement":
        raise ContractError("invalid product entitlement contract")
    _validate_brand_id(record["brand_id"])
    if record["module"] not in PRODUCT_MODULES:
        raise ContractError("unknown product module")
    if not isinstance(record["limits"], Mapping):
        raise ContractError("entitlement limits must be an object")
    canonical_bytes(record["limits"])
    if record["status"] != "active" or not record["entitlement_id"] or not record["issued_by"]:
        raise ContractError("entitlement must have an id, active status, and issuer")
    parse_time(record["issued_at"])


def _validated_stored_record(record_json: str, artifact_type: str, brand_id: str) -> dict[str, Any]:
    record = json.loads(record_json)
    if not isinstance(record, dict):
        raise FleetTenancyError("stored tenant authority record is not an object")
    verify_record(record)
    if record.get("artifact_type") != artifact_type or record.get("brand_id") != brand_id:
        raise FleetTenancyError("stored tenant authority binding is invalid")
    return record


def _validate_brand_id(value: object) -> None:
    if not isinstance(value, str) or not _BRAND_ID.fullmatch(value):
        raise ContractError("brand_id must be a normalised brand_ identifier")


def _normalise_hostname(value: object) -> str:
    if not isinstance(value, str):
        raise ContractError("hostname must be text")
    hostname = value.strip().lower().rstrip(".")
    if not hostname or ":" in hostname or "/" in hostname or len(hostname) > 253:
        raise ContractError("hostname must be a bare DNS name")
    return hostname


def _require_director(principal: Principal) -> None:
    if principal.role_id != "agency-director":
        raise FleetTenancyAuthorizationError("only the agency director may change tenant authority")


def _require_same_brand_director(principal: Principal, brand_id: str) -> None:
    _require_director(principal)
    if principal.brand_id != brand_id:
        raise FleetTenancyAuthorizationError("cross-tenant change denied")


def _require_reader(principal: Principal) -> None:
    if principal.role_id not in _READ_ROLES:
        raise FleetTenancyAuthorizationError("role cannot read tenant authority")


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass
