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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
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
_LIMIT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED_SLUGS = frozenset({"admin", "api", "app", "auth", "paperclip", "www"})
_READ_ROLES = frozenset(
    {"agency-director", "platform-assurance-reviewer", "brand-agent-service"}
)
_SCHEMA_VERSION = 3
_TENANT_LIFECYCLE = frozenset(
    {
        "provisioning", "launch_ready", "assurance", "active",
        "failed_pre_activation", "suspended", "offboarding", "offboarded",
    }
)
_LIFECYCLE_TRANSITIONS = {
    "provisioning": frozenset({"launch_ready", "failed_pre_activation"}),
    "launch_ready": frozenset({"assurance", "failed_pre_activation"}),
    "assurance": frozenset({"active", "failed_pre_activation"}),
    "active": frozenset({"suspended", "offboarding"}),
    "suspended": frozenset({"active", "offboarding"}),
    "offboarding": frozenset({"offboarded"}),
    "failed_pre_activation": frozenset({"provisioning"}),
    "offboarded": frozenset(),
}

_BRAND_TENANT_FIELDS = frozenset(
    {
        "schema_version", "artifact_type", "tenant_id", "brand_id",
        "paperclip_company_id", "company_name", "status", "created_by",
        "created_at", "content_checksum",
    }
)
_PORTAL_HOSTNAME_FIELDS = frozenset(
    {
        "schema_version", "artifact_type", "binding_id", "brand_id",
        "brand_slug", "hostname", "base_domain", "status", "created_by",
        "created_at", "content_checksum",
    }
)
_PRODUCT_ENTITLEMENT_FIELDS = frozenset(
    {
        "schema_version", "artifact_type", "entitlement_id", "brand_id",
        "module", "version", "limits", "status", "issued_by", "issued_at",
        "effective_at", "expires_at", "supersedes_entitlement_id",
        "content_checksum",
    }
)


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
    version: int = 1,
    limits: Mapping[str, Any] | None = None,
    issued_at: str | None = None,
    effective_at: str | None = None,
    expires_at: str | None = None,
    supersedes_entitlement_id: str | None = None,
) -> dict[str, Any]:
    """Build one immutable version in a module entitlement lifecycle."""

    issuance_time = issued_at or utc_now()

    record = {
        "schema_version": "2.0",
        "artifact_type": "product_entitlement",
        "entitlement_id": entitlement_id,
        "brand_id": brand_id,
        "module": module,
        "version": version,
        "limits": copy.deepcopy(dict(limits or {})),
        "status": "active",
        "issued_by": issued_by,
        "issued_at": issuance_time,
        "effective_at": effective_at or issuance_time,
        "expires_at": expires_at,
        "supersedes_entitlement_id": supersedes_entitlement_id,
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
        target_id = str(record.get("tenant_id", "invalid_tenant"))
        self._authorize_mutation(
            principal, "register_tenant", target_id,
            record.get("brand_id"), record.get("created_by"),
        )
        try:
            _validate_brand_tenant(record)
        except ContractError:
            self._audit(principal, "register_tenant", target_id, "DENY_CONTRACT")
            raise
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
        target_id = str(record.get("binding_id", "invalid_hostname"))
        self._authorize_mutation(
            principal, "register_hostname", target_id,
            record.get("brand_id"), record.get("created_by"),
        )
        try:
            _validate_portal_hostname(record)
        except ContractError:
            self._audit(principal, "register_hostname", target_id, "DENY_CONTRACT")
            raise
        return self._insert_immutable(
            principal,
            operation="register_hostname",
            table="portal_hostnames",
            identity_columns=("hostname", "brand_id", "binding_id"),
            identity_values=(record["hostname"], record["brand_id"], record["binding_id"]),
            record=record,
            target_id=record["binding_id"],
            require_active_tenant=True,
        )

    def grant_entitlement(self, principal: Principal, record: Mapping[str, Any]) -> str:
        target_id = str(record.get("entitlement_id", "invalid_entitlement"))
        self._authorize_mutation(
            principal, "grant_entitlement", target_id,
            record.get("brand_id"), record.get("issued_by"),
        )
        try:
            _validate_product_entitlement(record)
        except ContractError:
            self._audit(principal, "grant_entitlement", target_id, "DENY_CONTRACT")
            raise
        return self._insert_immutable(
            principal,
            operation="grant_entitlement",
            table="product_entitlements",
            identity_columns=("brand_id", "module", "version", "entitlement_id"),
            identity_values=(record["brand_id"], record["module"], record["version"], record["entitlement_id"]),
            record=record,
            target_id=record["entitlement_id"],
            require_active_tenant=True,
        )

    def initialize_bundle(
        self,
        principal: Principal,
        tenant: Mapping[str, Any],
        hostnames: Iterable[Mapping[str, Any]],
        entitlements: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Validate and commit an entire tenant foundation as one transaction."""

        hostname_records = tuple(hostnames)
        entitlement_records = tuple(entitlements)
        self._validate_mutation_record(
            principal, "register_tenant", tenant, "tenant_id", "created_by",
            _validate_brand_tenant,
        )
        for record in hostname_records:
            self._validate_mutation_record(
                principal, "register_hostname", record, "binding_id", "created_by",
                _validate_portal_hostname,
            )
        for record in entitlement_records:
            self._validate_mutation_record(
                principal, "grant_entitlement", record, "entitlement_id", "issued_by",
                _validate_product_entitlement,
            )

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_immutable_tx(
                connection, principal,
                operation="register_tenant", table="brand_tenants",
                identity_columns=("brand_id", "tenant_id", "paperclip_company_id"),
                identity_values=(tenant["brand_id"], tenant["tenant_id"], tenant["paperclip_company_id"]),
                record=tenant, target_id=tenant["tenant_id"],
            )
            for record in hostname_records:
                self._insert_immutable_tx(
                    connection, principal,
                    operation="register_hostname", table="portal_hostnames",
                    identity_columns=("hostname", "brand_id", "binding_id"),
                    identity_values=(record["hostname"], record["brand_id"], record["binding_id"]),
                    record=record, target_id=record["binding_id"],
                    require_active_tenant=True,
                )
            for record in entitlement_records:
                self._insert_immutable_tx(
                    connection, principal,
                    operation="grant_entitlement", table="product_entitlements",
                    identity_columns=("brand_id", "module", "version", "entitlement_id"),
                    identity_values=(record["brand_id"], record["module"], record["version"], record["entitlement_id"]),
                    record=record, target_id=record["entitlement_id"],
                    require_active_tenant=True,
                )
            self._insert_audit(
                connection, principal, "initialize_bundle", tenant["tenant_id"], "ALLOW_ATOMIC",
            )
            connection.commit()
        except (ContractError, FleetTenancyAuthorizationError):
            _rollback(connection)
            self._audit(principal, "initialize_bundle", str(tenant.get("tenant_id")), "DENY_ATOMIC")
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            self._audit(principal, "initialize_bundle", str(tenant.get("tenant_id")), "DENY_ERROR")
            raise FleetTenancyError("could not initialize tenant bundle") from exc
        finally:
            connection.close()
        return {
            "tenant_id": tenant["tenant_id"],
            "hostnames": sorted(record["hostname"] for record in hostname_records),
            "enabled_modules": sorted(record["module"] for record in entitlement_records),
        }

    def suspend_tenant(self, principal: Principal, brand_id: str) -> None:
        self._authorize_mutation(
            principal, "suspend_tenant", brand_id, brand_id, None,
        )
        self._suspend(principal, "brand_tenants", "brand_id", brand_id, "suspend_tenant")

    def suspend_hostname(self, principal: Principal, hostname: str) -> None:
        target_id = str(hostname)
        self._authorize_mutation(
            principal, "suspend_hostname", target_id, principal.brand_id, None,
        )
        try:
            normalised = _normalise_hostname(hostname)
        except ContractError:
            self._audit(principal, "suspend_hostname", target_id, "DENY_CONTRACT")
            raise
        self._suspend(
            principal,
            "portal_hostnames",
            "hostname",
            normalised,
            "suspend_hostname",
            brand_column="brand_id",
        )

    def suspend_entitlement(self, principal: Principal, module: str) -> None:
        self._authorize_mutation(
            principal, "suspend_entitlement", str(module), principal.brand_id, None,
        )
        if module not in PRODUCT_MODULES:
            self._audit(principal, "suspend_entitlement", str(module), "DENY_CONTRACT")
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
            ORDER BY e.version DESC LIMIT 1
            """,
            (principal.brand_id, module),
        )
        if row is None or row[1] != "active" or row[2] != "active":
            self._audit(principal, "module_enabled", module, "DISABLED")
            return False
        record = _validated_stored_record(row[0], "product_entitlement", principal.brand_id)
        if record.get("module") != module:
            raise FleetTenancyError("stored entitlement key does not match its record")
        if not _entitlement_is_effective(record):
            self._audit(principal, "module_enabled", module, "DISABLED_TIME_WINDOW")
            return False
        self._audit(principal, "module_enabled", module, "ENABLED")
        return True

    def portal_read_model(self, principal: Principal, hostname: str) -> dict[str, Any]:
        """Return a client-safe, server-built portal routing projection."""

        if not self.module_enabled(principal, "client_portal"):
            self._audit(principal, "portal_read_model", str(hostname), "DENY_ENTITLEMENT")
            raise FleetTenancyAuthorizationError("the client_portal module is not enabled")
        binding = self.authorize_hostname(principal, hostname)
        tenant = self.get_tenant(principal)
        modules = {module: self.module_enabled(principal, module) for module in sorted(PRODUCT_MODULES)}
        return {
            "schema_version": "2.0",
            "brand_id": principal.brand_id,
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "hostname": binding["hostname"],
            "brand_slug": binding["brand_slug"],
            "modules": modules,
        }

    def admit_account_brand(
        self,
        principal: Principal,
        *,
        customer_account_id: str,
        account_name: str,
        client_brand_id: str,
        client_brand_name: str,
        tenant_id: str,
        workos_organization_id: str,
        lifecycle_state: str = "provisioning",
    ) -> dict[str, Any]:
        """Bind account, identity and client-brand concepts to a tenant."""

        self._authorize_mutation(
            principal, "admit_account_brand", client_brand_id,
            principal.brand_id, principal.actor_id,
        )
        values = (
            customer_account_id, account_name, client_brand_id,
            client_brand_name, tenant_id, workos_organization_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in values):
            self._audit(principal, "admit_account_brand", client_brand_id, "DENY_CONTRACT")
            raise ContractError("account and brand identifiers must be non-empty strings")
        if not customer_account_id.startswith("account_"):
            raise ContractError("customer_account_id must use the account_ prefix")
        if not client_brand_id.startswith("client_brand_"):
            raise ContractError("client_brand_id must use the client_brand_ prefix")
        if lifecycle_state not in _TENANT_LIFECYCLE:
            raise ContractError("unknown tenant lifecycle state")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            tenant = connection.execute(
                "SELECT brand_id FROM brand_tenants WHERE tenant_id = ?", (tenant_id,),
            ).fetchone()
            if tenant is None or tenant[0] != principal.brand_id:
                raise FleetTenancyAuthorizationError(
                    "account admission requires the principal's immutable tenant binding"
                )
            now = utc_now()
            existing = connection.execute(
                """
                SELECT a.account_name, a.workos_organization_id,
                       b.client_brand_name, b.customer_account_id,
                       b.tenant_id, b.brand_id, b.lifecycle_state
                FROM customer_accounts a
                JOIN client_brands b ON b.customer_account_id = a.customer_account_id
                WHERE a.customer_account_id = ? AND b.client_brand_id = ?
                """,
                (customer_account_id, client_brand_id),
            ).fetchone()
            expected = (
                account_name, workos_organization_id, client_brand_name,
                customer_account_id, tenant_id, principal.brand_id, lifecycle_state,
            )
            if existing is not None:
                if tuple(existing) != expected:
                    raise ContractError("account or client brand binding is immutable")
                outcome = "ALLOW_IDEMPOTENT"
            else:
                connection.execute(
                    """
                    INSERT INTO customer_accounts (
                        customer_account_id, account_name, workos_organization_id,
                        state, created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (customer_account_id, account_name, workos_organization_id, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO client_brands (
                        client_brand_id, customer_account_id, client_brand_name,
                        tenant_id, brand_id, lifecycle_state, lifecycle_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        client_brand_id, customer_account_id, client_brand_name,
                        tenant_id, principal.brand_id, lifecycle_state, now, now,
                    ),
                )
                outcome = "ALLOW"
            self._insert_audit(
                connection, principal, "admit_account_brand", client_brand_id, outcome,
            )
            connection.commit()
        except (ContractError, FleetTenancyAuthorizationError):
            _rollback(connection)
            self._audit(principal, "admit_account_brand", client_brand_id, "DENY_WRITE")
            raise
        except sqlite3.IntegrityError as exc:
            _rollback(connection)
            self._audit(
                principal, "admit_account_brand", client_brand_id,
                "DENY_IMMUTABLE_CONFLICT",
            )
            raise ContractError(
                "account, identity organisation, brand or tenant is already bound"
            ) from exc
        except sqlite3.Error as exc:
            _rollback(connection)
            raise FleetTenancyError("could not admit customer account and client brand") from exc
        finally:
            connection.close()
        return self.account_brand_projection(principal)

    def transition_tenant_lifecycle(
        self,
        principal: Principal,
        *,
        client_brand_id: str,
        expected_version: int,
        next_state: str,
    ) -> dict[str, Any]:
        """Apply an explicit, optimistic lifecycle transition."""

        self._authorize_mutation(
            principal, "transition_tenant_lifecycle", client_brand_id,
            principal.brand_id, principal.actor_id,
        )
        if next_state not in _TENANT_LIFECYCLE:
            raise ContractError("unknown tenant lifecycle state")
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ContractError("expected lifecycle version must be a positive integer")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT lifecycle_state, lifecycle_version FROM client_brands
                WHERE client_brand_id = ? AND brand_id = ?
                """,
                (client_brand_id, principal.brand_id),
            ).fetchone()
            if row is None:
                raise KeyError(client_brand_id)
            current_state, current_version = str(row[0]), int(row[1])
            if current_version != expected_version:
                raise ContractError("tenant lifecycle version conflict")
            if next_state not in _LIFECYCLE_TRANSITIONS[current_state]:
                raise ContractError(
                    f"tenant lifecycle cannot move from {current_state} to {next_state}"
                )
            connection.execute(
                """
                UPDATE client_brands
                SET lifecycle_state = ?, lifecycle_version = ?, updated_at = ?
                WHERE client_brand_id = ? AND brand_id = ?
                """,
                (
                    next_state, current_version + 1, utc_now(),
                    client_brand_id, principal.brand_id,
                ),
            )
            self._insert_audit(
                connection, principal, "transition_tenant_lifecycle", client_brand_id,
                f"ALLOW_{current_state.upper()}_TO_{next_state.upper()}",
            )
            connection.commit()
        except KeyError:
            _rollback(connection)
            self._audit(
                principal, "transition_tenant_lifecycle", client_brand_id,
                "DENY_NOT_FOUND",
            )
            raise
        except ContractError:
            _rollback(connection)
            self._audit(
                principal, "transition_tenant_lifecycle", client_brand_id,
                "DENY_TRANSITION",
            )
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise FleetTenancyError("could not transition tenant lifecycle") from exc
        finally:
            connection.close()
        return self.account_brand_projection(principal)

    def account_brand_projection(self, principal: Principal) -> dict[str, Any]:
        """Return the safe account/brand/tenant projection for one principal."""

        _require_reader(principal)
        row = self._fetch_one(
            """
            SELECT a.customer_account_id, a.account_name,
                   b.client_brand_id, b.client_brand_name, b.tenant_id,
                   b.lifecycle_state, b.lifecycle_version
            FROM customer_accounts a
            JOIN client_brands b ON b.customer_account_id = a.customer_account_id
            WHERE b.brand_id = ?
            """,
            (principal.brand_id,),
        )
        if row is None:
            self._audit(
                principal, "account_brand_projection", principal.brand_id,
                "NOT_FOUND_OR_WRONG_TENANT",
            )
            raise KeyError(principal.brand_id)
        self._audit(principal, "account_brand_projection", str(row[2]), "ALLOW")
        return {
            "customer_account_id": row[0],
            "account_name": row[1],
            "client_brand_id": row[2],
            "client_brand_name": row[3],
            "tenant_id": row[4],
            "brand_id": principal.brand_id,
            "lifecycle_state": row[5],
            "lifecycle_version": row[6],
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

    def _authorize_mutation(
        self,
        principal: Principal,
        operation: str,
        target_id: str,
        brand_id: object,
        creator_id: object | None,
    ) -> None:
        if principal.role_id != "agency-director":
            self._audit(principal, operation, target_id, "DENY_ROLE")
            raise FleetTenancyAuthorizationError("only the agency director may change tenant authority")
        if brand_id != principal.brand_id:
            self._audit(principal, operation, target_id, "DENY_TENANT")
            raise FleetTenancyAuthorizationError("cross-tenant change denied")
        if creator_id is not None and creator_id != principal.actor_id:
            self._audit(principal, operation, target_id, "DENY_ACTOR")
            raise FleetTenancyAuthorizationError("record actor does not match principal")

    def _validate_mutation_record(
        self,
        principal: Principal,
        operation: str,
        record: Mapping[str, Any],
        target_field: str,
        creator_field: str,
        validator: Any,
    ) -> None:
        target_id = str(record.get(target_field, f"invalid_{target_field}"))
        self._authorize_mutation(
            principal, operation, target_id,
            record.get("brand_id"), record.get(creator_field),
        )
        try:
            validator(record)
        except ContractError:
            self._audit(principal, operation, target_id, "DENY_CONTRACT")
            raise

    def _insert_immutable(
        self,
        principal: Principal,
        *,
        operation: str,
        table: str,
        identity_columns: tuple[str, ...],
        identity_values: tuple[Any, ...],
        record: Mapping[str, Any],
        target_id: str,
        require_active_tenant: bool = False,
    ) -> str:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_immutable_tx(
                connection, principal,
                operation=operation, table=table,
                identity_columns=identity_columns, identity_values=identity_values,
                record=record, target_id=target_id,
                require_active_tenant=require_active_tenant,
            )
            connection.commit()
        except (ContractError, FleetTenancyAuthorizationError):
            _rollback(connection)
            self._audit(principal, operation, target_id, "DENY_WRITE")
            raise
        except sqlite3.IntegrityError as exc:
            _rollback(connection)
            self._audit(principal, operation, target_id, "DENY_IMMUTABLE_CONFLICT")
            raise ContractError(f"{target_id!r} conflicts with an immutable binding") from exc
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise FleetTenancyError(f"could not {operation}") from exc
        finally:
            connection.close()
        return target_id

    def _insert_immutable_tx(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        *,
        operation: str,
        table: str,
        identity_columns: tuple[str, ...],
        identity_values: tuple[Any, ...],
        record: Mapping[str, Any],
        target_id: str,
        require_active_tenant: bool = False,
    ) -> None:
        allowed = {
            "brand_tenants": ("brand_id", "tenant_id", "paperclip_company_id"),
            "portal_hostnames": ("hostname", "brand_id", "binding_id"),
            "product_entitlements": ("brand_id", "module", "version", "entitlement_id"),
        }
        if allowed.get(table) != identity_columns:
            raise FleetTenancyError("invalid immutable table binding")
        if require_active_tenant:
            tenant = connection.execute(
                "SELECT state FROM brand_tenants WHERE brand_id = ?",
                (record["brand_id"],),
            ).fetchone()
            if tenant is None or tenant[0] != "active":
                raise FleetTenancyAuthorizationError("an active tenant binding is required")

        lookup_count = 3 if table == "product_entitlements" else 1
        lookup_columns = identity_columns[:lookup_count]
        lookup_values = identity_values[:lookup_count]
        lookup_where = " AND ".join(f"{column} = ?" for column in lookup_columns)
        existing = connection.execute(
            f"SELECT record_json FROM {table} WHERE {lookup_where}", lookup_values,
        ).fetchone()
        if existing is not None:
            if json.loads(existing[0]) != dict(record):
                raise ContractError(f"{target_id!r} is immutable")
            self._insert_audit(connection, principal, operation, target_id, "ALLOW_IDEMPOTENT")
            return

        if table == "product_entitlements":
            latest = connection.execute(
                """
                SELECT version, entitlement_id FROM product_entitlements
                WHERE brand_id = ? AND module = ? ORDER BY version DESC LIMIT 1
                """,
                (record["brand_id"], record["module"]),
            ).fetchone()
            if latest is None:
                if record["version"] != 1 or record["supersedes_entitlement_id"] is not None:
                    raise ContractError("the first entitlement version must be 1 and supersede nothing")
            elif (
                record["version"] != int(latest[0]) + 1
                or record["supersedes_entitlement_id"] != latest[1]
            ):
                raise ContractError("entitlement versions must be contiguous and link to the latest version")

        record_json = canonical_bytes(dict(record)).decode("utf-8")
        columns = (*identity_columns, "record_json", "state", "created_at", "updated_at")
        placeholders = ", ".join("?" for _ in columns)
        now = utc_now()
        connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            (*identity_values, record_json, "active", now, now),
        )
        self._insert_audit(connection, principal, operation, target_id, "ALLOW")

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
            ordering = " ORDER BY version DESC" if table == "product_entitlements" else ""
            row = connection.execute(
                f"SELECT rowid FROM {table} WHERE {where}{ordering} LIMIT 1", parameters,
            ).fetchone()
            if row is None:
                raise KeyError(key_value)
            connection.execute(
                f"UPDATE {table} SET state = 'suspended', updated_at = ? WHERE rowid = ?",
                (utc_now(), row[0]),
            )
            self._insert_audit(connection, principal, operation, key_value, "SUSPEND")
            connection.commit()
        except KeyError:
            _rollback(connection)
            self._audit(principal, operation, key_value, "DENY_NOT_FOUND")
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise FleetTenancyError(f"could not {operation}") from exc
        finally:
            connection.close()

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
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise FleetTenancyError("tenant authority could not enable WAL mode")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_metadata "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
            )
            row = connection.execute("SELECT version FROM schema_metadata WHERE id = 1").fetchone()
            current_version = None if row is None else int(row[0])
            if current_version is not None and current_version > _SCHEMA_VERSION:
                raise FleetTenancyError("tenant authority schema is newer than this runtime")
            if current_version not in (None, 1, 2, _SCHEMA_VERSION):
                raise FleetTenancyError("tenant authority schema has no supported migration path")

            self._create_schema_v2(connection)
            if current_version == 1:
                self._migrate_v1_to_v2(connection)
            self._create_schema_v3(connection)
            connection.execute(
                "INSERT INTO schema_metadata (id, version) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
                (_SCHEMA_VERSION,),
            )
            self._validate_schema_v3(connection)
            connection.commit()
        except (ContractError, FleetTenancyError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise FleetTenancyError("could not initialize tenant authority") from exc
        finally:
            connection.close()

    @staticmethod
    def _create_schema_v2(connection: sqlite3.Connection) -> None:
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
                version INTEGER NOT NULL CHECK (version >= 1),
                entitlement_id TEXT NOT NULL UNIQUE,
                record_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('active', 'suspended')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (brand_id, module, version),
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

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(product_entitlements)")
        }
        if "version" in columns:
            return
        connection.execute("ALTER TABLE product_entitlements RENAME TO product_entitlements_v1")
        self._create_schema_v2(connection)
        rows = connection.execute(
            """
            SELECT brand_id, module, entitlement_id, record_json, state, created_at, updated_at
            FROM product_entitlements_v1 ORDER BY brand_id, module
            """
        ).fetchall()
        for brand_id, module, entitlement_id, record_json, state, created_at, updated_at in rows:
            record = json.loads(record_json)
            verify_record(record)
            migrated = dict(record)
            migrated.update(
                {
                    "version": 1,
                    "effective_at": "1970-01-01T00:00:00Z",
                    "expires_at": None,
                    "supersedes_entitlement_id": None,
                }
            )
            migrated = finalize_record(migrated)
            _validate_product_entitlement(migrated)
            connection.execute(
                """
                INSERT INTO product_entitlements (
                    brand_id, module, version, entitlement_id, record_json,
                    state, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    brand_id, module, entitlement_id,
                    canonical_bytes(migrated).decode("utf-8"),
                    state, created_at, updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO authority_audit (
                    actor_id, role_id, brand_id, operation, target_id, outcome, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "fleet-schema-migrator", "platform-assurance-reviewer", brand_id,
                    "migrate_entitlement_v1_to_v2", entitlement_id, "ALLOW", utc_now(),
                ),
            )
        connection.execute("DROP TABLE product_entitlements_v1")

    @staticmethod
    def _create_schema_v3(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_accounts (
                customer_account_id TEXT PRIMARY KEY,
                account_name TEXT NOT NULL,
                workos_organization_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (state IN ('active', 'suspended')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS client_brands (
                client_brand_id TEXT PRIMARY KEY,
                customer_account_id TEXT NOT NULL,
                client_brand_name TEXT NOT NULL,
                tenant_id TEXT NOT NULL UNIQUE,
                brand_id TEXT NOT NULL UNIQUE,
                lifecycle_state TEXT NOT NULL CHECK (
                    lifecycle_state IN (
                        'provisioning', 'launch_ready', 'assurance', 'active',
                        'failed_pre_activation', 'suspended', 'offboarding',
                        'offboarded'
                    )
                ),
                lifecycle_version INTEGER NOT NULL CHECK (lifecycle_version >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (customer_account_id)
                    REFERENCES customer_accounts(customer_account_id),
                FOREIGN KEY (brand_id) REFERENCES brand_tenants(brand_id),
                FOREIGN KEY (tenant_id) REFERENCES brand_tenants(tenant_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hostname_tombstones (
                hostname TEXT PRIMARY KEY,
                former_brand_id TEXT NOT NULL,
                retired_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provisioning_runs (
                provisioning_run_id TEXT PRIMARY KEY,
                brand_id TEXT NOT NULL,
                client_brand_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('running', 'blocked', 'completed', 'failed')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (brand_id) REFERENCES brand_tenants(brand_id),
                FOREIGN KEY (client_brand_id)
                    REFERENCES client_brands(client_brand_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provisioning_steps (
                provisioning_run_id TEXT NOT NULL,
                step_key TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('pending', 'running', 'completed', 'failed',
                              'compensated')
                ),
                evidence_checksum TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (provisioning_run_id, step_key),
                FOREIGN KEY (provisioning_run_id)
                    REFERENCES provisioning_runs(provisioning_run_id)
            )
            """
        )

    @staticmethod
    def _validate_schema_v3(connection: sqlite3.Connection) -> None:
        expected_columns = {
            "brand_tenants": {
                "brand_id", "tenant_id", "paperclip_company_id", "record_json",
                "state", "created_at", "updated_at",
            },
            "portal_hostnames": {
                "hostname", "brand_id", "binding_id", "record_json",
                "state", "created_at", "updated_at",
            },
            "product_entitlements": {
                "brand_id", "module", "version", "entitlement_id", "record_json",
                "state", "created_at", "updated_at",
            },
            "authority_audit": {
                "sequence", "actor_id", "role_id", "brand_id", "operation",
                "target_id", "outcome", "recorded_at",
            },
            "customer_accounts": {
                "customer_account_id", "account_name", "workos_organization_id",
                "state", "created_at", "updated_at",
            },
            "client_brands": {
                "client_brand_id", "customer_account_id", "client_brand_name",
                "tenant_id", "brand_id", "lifecycle_state",
                "lifecycle_version", "created_at", "updated_at",
            },
            "hostname_tombstones": {
                "hostname", "former_brand_id", "retired_at", "reason",
            },
            "provisioning_runs": {
                "provisioning_run_id", "brand_id", "client_brand_id", "state",
                "created_at", "updated_at",
            },
            "provisioning_steps": {
                "provisioning_run_id", "step_key", "state",
                "evidence_checksum", "updated_at",
            },
        }
        for table, expected in expected_columns.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            if {row[1] for row in rows} != expected:
                raise FleetTenancyError(f"tenant authority table {table} has an invalid shape")
        entitlement_info = connection.execute(
            "PRAGMA table_info(product_entitlements)"
        ).fetchall()
        primary_key = [row[1] for row in sorted(entitlement_info, key=lambda row: row[5]) if row[5]]
        if primary_key != ["brand_id", "module", "version"]:
            raise FleetTenancyError("product entitlement primary key is invalid")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise FleetTenancyError("tenant authority foreign key check failed")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise FleetTenancyError("tenant authority integrity check failed")

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
    _reject_unknown_fields(record, _BRAND_TENANT_FIELDS)
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
    _reject_unknown_fields(record, _PORTAL_HOSTNAME_FIELDS)
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
    _reject_unknown_fields(record, _PRODUCT_ENTITLEMENT_FIELDS)
    require_fields(
        record,
        (
            "schema_version", "artifact_type", "entitlement_id", "brand_id",
            "module", "version", "limits", "status", "issued_by", "issued_at",
            "effective_at", "expires_at", "supersedes_entitlement_id",
        ),
    )
    if record["schema_version"] != "2.0" or record["artifact_type"] != "product_entitlement":
        raise ContractError("invalid product entitlement contract")
    _validate_brand_id(record["brand_id"])
    if record["module"] not in PRODUCT_MODULES:
        raise ContractError("unknown product module")
    version = record["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractError("entitlement version must be a positive integer")
    if not isinstance(record["limits"], Mapping):
        raise ContractError("entitlement limits must be an object")
    for key, value in record["limits"].items():
        if not isinstance(key, str) or not _LIMIT_KEY.fullmatch(key):
            raise ContractError("entitlement limit keys must be normalised identifiers")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int) and value >= 0:
            continue
        if isinstance(value, str) and value.strip():
            continue
        raise ContractError("entitlement limit values must be booleans, non-negative integers, non-empty strings, or null")
    canonical_bytes(record["limits"])
    if record["status"] != "active" or not record["entitlement_id"] or not record["issued_by"]:
        raise ContractError("entitlement must have an id, active status, and issuer")
    parse_time(record["issued_at"])
    effective_at = parse_time(record["effective_at"])
    expires_at = record["expires_at"]
    if expires_at is not None and parse_time(expires_at) <= effective_at:
        raise ContractError("entitlement expiry must follow its effective time")
    supersedes = record["supersedes_entitlement_id"]
    if version == 1 and supersedes is not None:
        raise ContractError("the first entitlement version cannot supersede another")
    if version > 1 and (not isinstance(supersedes, str) or not supersedes):
        raise ContractError("later entitlement versions must name the version they supersede")


def _validated_stored_record(record_json: str, artifact_type: str, brand_id: str) -> dict[str, Any]:
    record = json.loads(record_json)
    if not isinstance(record, dict):
        raise FleetTenancyError("stored tenant authority record is not an object")
    validators = {
        "brand_tenant": _validate_brand_tenant,
        "portal_hostname_binding": _validate_portal_hostname,
        "product_entitlement": _validate_product_entitlement,
    }
    try:
        validators[artifact_type](record)
    except (ContractError, KeyError) as exc:
        raise FleetTenancyError("stored tenant authority record violates its contract") from exc
    if record.get("artifact_type") != artifact_type or record.get("brand_id") != brand_id:
        raise FleetTenancyError("stored tenant authority binding is invalid")
    return record


def _reject_unknown_fields(record: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(record).difference(allowed))
    if unknown:
        raise ContractError(f"unknown fields are not allowed: {', '.join(unknown)}")


def _entitlement_is_effective(
    record: Mapping[str, Any], at: datetime | None = None,
) -> bool:
    observed_at = at or datetime.now(timezone.utc)
    effective_at = parse_time(record["effective_at"])
    expires_at = record["expires_at"]
    return effective_at <= observed_at and (
        expires_at is None or observed_at < parse_time(expires_at)
    )


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
