"""Protected G2.6 portal identity, source, command and catalogue authority.

The browser never supplies a trusted tenant key.  A verified identity session
is joined to a stored membership and exact host binding to create the request
context. Paperclip remains the work and approval authority; portal commands are
delivery records with explicit reconciliation states.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import canonical_bytes, utc_now
from .sqlite_storage import SQLiteStorageError, prepare_sqlite_storage, validate_sqlite_storage


class FleetPortalError(RuntimeError):
    """The portal authority could not safely complete an operation."""


class FleetPortalAuthorizationError(PermissionError):
    """The requested operation is outside the verified portal context."""


CLIENT_ROLES = frozenset({"owner", "approver", "contributor", "analyst", "viewer"})
FLEET_ROLES = frozenset(
    {"platform_administrator", "account_director", "operator", "assurance", "support"}
)
APPROVAL_SCOPES = frozenset(
    {"brand_fact", "claim", "content", "publication", "access_change", "commercial_change"}
)
COMMAND_STATES = frozenset(
    {
        "received", "dispatching", "authority_recorded", "projecting", "completed",
        "rejected", "conflict", "unknown", "cancelled",
    }
)
SOURCE_STATES = frozenset(
    {"quarantined", "scanning", "extracted", "review_required", "admitted", "rejected", "failed"}
)
_COMMAND_TRANSITIONS = {
    "received": frozenset({"dispatching", "rejected", "cancelled"}),
    "dispatching": frozenset({"authority_recorded", "unknown", "rejected", "conflict"}),
    "authority_recorded": frozenset({"projecting", "unknown"}),
    "projecting": frozenset({"completed", "unknown"}),
    "unknown": frozenset({"dispatching", "authority_recorded", "rejected", "conflict"}),
    "completed": frozenset(), "rejected": frozenset(), "conflict": frozenset(),
    "cancelled": frozenset(),
}
_ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "xlsx", "csv", "txt", "png", "jpg", "jpeg"})
_BLOCKED_SUFFIXES = frozenset(
    {"zip", "tar", "gz", "7z", "rar", "exe", "dll", "dmg", "pkg", "sh", "js", "docm", "xlsm"}
)
_HOST = re.compile(r"^[a-z0-9-]+\.madebyfleet\.com$")
_SCHEMA_VERSION = 1
_SESSION_IDLE_LIMIT = timedelta(minutes=60)
_SESSION_ABSOLUTE_LIMIT = timedelta(hours=12)
_COMMAND_RATE_LIMIT_PER_MINUTE = 20


@dataclass(frozen=True)
class PortalRequestContext:
    hostname: str
    origin: str
    workos_subject: str
    workos_organization_id: str
    customer_account_id: str
    client_brand_id: str
    tenant_id: str
    brand_id: str
    client_role: str
    approval_scopes: tuple[str, ...]
    session_id: str
    entitlement_version: int
    correlation_id: str


def payload_checksum(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(dict(payload))).hexdigest()


class SourceAdmissionPolicy:
    """Content-aware upload and URL checks applied before extraction."""

    maximum_file_bytes = 50 * 1024 * 1024
    maximum_case_bytes = 250 * 1024 * 1024
    maximum_url_bytes = 10 * 1024 * 1024
    maximum_redirects = 3
    url_timeout_seconds = 15

    @classmethod
    def inspect_upload(
        cls,
        *,
        filename: str,
        declared_content_type: str,
        content: bytes,
        current_case_bytes: int = 0,
        malware_clean: bool,
    ) -> dict[str, Any]:
        if not isinstance(content, bytes):
            raise FleetPortalError("upload content must be bytes")
        name = Path(filename).name
        if name != filename or not name or "." not in name:
            raise FleetPortalError("upload filename is unsafe or has no extension")
        suffix = name.rsplit(".", 1)[1].lower()
        if suffix in _BLOCKED_SUFFIXES or suffix not in _ALLOWED_EXTENSIONS:
            raise FleetPortalError("upload type is not allowed")
        if len(content) == 0 or len(content) > cls.maximum_file_bytes:
            raise FleetPortalError("upload is empty or exceeds the per-file limit")
        if current_case_bytes < 0 or current_case_bytes + len(content) > cls.maximum_case_bytes:
            raise FleetPortalError("Launch Room upload limit would be exceeded")
        if not malware_clean:
            raise FleetPortalError("upload did not pass malware scanning")
        detected = cls._detect_type(content, suffix)
        if detected != suffix and not (suffix == "jpeg" and detected == "jpg"):
            raise FleetPortalError("upload extension does not match its content")
        return {
            "filename": name,
            "declared_content_type": declared_content_type,
            "detected_type": detected,
            "size_bytes": len(content),
            "content_checksum": "sha256:" + hashlib.sha256(content).hexdigest(),
            "malware_scan": "clean",
        }

    @staticmethod
    def _detect_type(content: bytes, suffix: str) -> str:
        if content.startswith(b"%PDF-"):
            return "pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if content.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    names = set(archive.namelist())
                    if "word/document.xml" in names and not any("vbaProject" in name for name in names):
                        return "docx"
                    if "xl/workbook.xml" in names and not any("vbaProject" in name for name in names):
                        return "xlsx"
            except (OSError, zipfile.BadZipFile):
                pass
            raise FleetPortalError("Office document container is invalid or macro-enabled")
        if b"\x00" in content:
            raise FleetPortalError("text upload contains binary content")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FleetPortalError("text upload must be valid UTF-8") from exc
        return suffix if suffix in {"csv", "txt"} else "text"

    @classmethod
    def validate_url_hop(cls, url: str, resolved_addresses: Iterable[str]) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise FleetPortalError("source URL must be credential-free HTTPS")
        if parsed.port not in (None, 443) or not parsed.hostname:
            raise FleetPortalError("source URL must use HTTPS port 443")
        addresses = tuple(resolved_addresses)
        if not addresses:
            raise FleetPortalError("source URL did not resolve")
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise FleetPortalError("source URL resolved to an invalid address") from exc
            if not address.is_global:
                raise FleetPortalError("source URL resolved to a non-public address")
        return parsed.hostname.lower().rstrip(".")


class FleetPortalAuthority:
    """Durable, brand-scoped portal authority backed by protected SQLite."""

    def __init__(self, database_path: str | os.PathLike[str], *, timeout_seconds: float = 5.0) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("FleetPortalAuthority requires a durable file path")
        try:
            self._storage_identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise FleetPortalError("unsafe portal authority storage") from exc
        self.timeout_seconds = timeout_seconds
        self._initialize()

    def register_membership(
        self,
        *,
        actor_id: str,
        membership_id: str,
        workos_subject: str,
        workos_organization_id: str,
        customer_account_id: str,
        client_brand_id: str,
        tenant_id: str,
        brand_id: str,
        client_role: str,
        approval_scopes: Iterable[str],
        hostname: str,
        entitlement_version: int,
    ) -> None:
        role = client_role.lower()
        scopes = tuple(sorted(set(approval_scopes)))
        host = _normalise_host(hostname)
        if role not in CLIENT_ROLES or any(scope not in APPROVAL_SCOPES for scope in scopes):
            raise FleetPortalAuthorizationError("membership role or approval scope is invalid")
        if entitlement_version < 1:
            raise FleetPortalError("entitlement version must be positive")
        connection = self._connect()
        now = utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = (
                membership_id, workos_subject, workos_organization_id, customer_account_id,
                client_brand_id, tenant_id, brand_id, role, json.dumps(scopes), host,
                entitlement_version, "active", now, now,
            )
            existing = connection.execute(
                "SELECT workos_subject, workos_organization_id, customer_account_id, client_brand_id, "
                "tenant_id, brand_id, client_role, scopes_json, hostname, entitlement_version, state "
                "FROM memberships WHERE membership_id = ?",
                (membership_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != record[1:12]:
                    raise FleetPortalError("membership is immutable; revoke and issue a new membership")
            else:
                connection.execute(
                    "INSERT INTO memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", record,
                )
            self._audit(connection, actor_id, brand_id, "register_membership", membership_id, "ALLOW")
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise FleetPortalError("identity or membership conflicts with another tenant") from exc
        finally:
            connection.close()

    def create_session(
        self,
        *,
        session_id: str,
        membership_id: str,
        workos_subject: str,
        workos_organization_id: str,
        expires_at: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            membership = connection.execute(
                "SELECT workos_subject, workos_organization_id, brand_id, state "
                "FROM memberships WHERE membership_id = ?", (membership_id,),
            ).fetchone()
            if membership is None or membership[3] != "active":
                raise FleetPortalAuthorizationError("active membership is required")
            if (membership[0], membership[1]) != (workos_subject, workos_organization_id):
                raise FleetPortalAuthorizationError("identity does not match membership")
            now = utc_now()
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
                (session_id, membership_id, workos_subject, workos_organization_id, now, now, expires_at),
            )
            self._audit(connection, workos_subject, membership[2], "create_session", session_id, "ALLOW")
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise FleetPortalError("session identifier is already in use") from exc
        finally:
            connection.close()

    def resolve_verified_identity(
        self,
        *,
        workos_subject: str,
        workos_organization_id: str,
        hostname: str,
        origin: str,
        access_identity_verified: bool,
        session_id: str,
        correlation_id: str,
    ) -> PortalRequestContext:
        """Resolve identity supplied by the authenticated, peer-checked web service."""

        host = _normalise_host(hostname)
        if not access_identity_verified or origin != f"https://{host}":
            raise FleetPortalAuthorizationError("edge identity and exact origin are required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT membership_id, customer_account_id, client_brand_id, tenant_id, brand_id,
                       client_role, scopes_json, entitlement_version, state
                FROM memberships
                WHERE workos_subject = ? AND workos_organization_id = ? AND hostname = ?
                """,
                (workos_subject, workos_organization_id, host),
            ).fetchone()
            if row is None or row[8] != "active":
                raise FleetPortalAuthorizationError(
                    "identity has no active membership for this organisation and host"
                )
            now = utc_now()
            existing_session = connection.execute(
                "SELECT membership_id, state, created_at, last_seen_at, expires_at "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing_session is not None and (
                existing_session[0] != row[0] or existing_session[1] != "active"
            ):
                raise FleetPortalAuthorizationError("the verified session is revoked or rebound")
            current = datetime.now(timezone.utc)
            expires_at = (current + _SESSION_ABSOLUTE_LIMIT).isoformat().replace("+00:00", "Z")
            if existing_session is None:
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
                    (
                        session_id, row[0], workos_subject, workos_organization_id,
                        now, now, expires_at,
                    ),
                )
            else:
                created_at = _parse_timestamp(existing_session[2])
                last_seen_at = _parse_timestamp(existing_session[3])
                absolute_expiry = min(
                    _parse_timestamp(existing_session[4]), created_at + _SESSION_ABSOLUTE_LIMIT,
                )
                if current - last_seen_at > _SESSION_IDLE_LIMIT or current >= absolute_expiry:
                    connection.execute(
                        "UPDATE sessions SET state = 'expired', last_seen_at = ? WHERE session_id = ?",
                        (now, session_id),
                    )
                    connection.commit()
                    raise FleetPortalAuthorizationError("the verified session has expired")
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
            connection.commit()
        finally:
            connection.close()
        return PortalRequestContext(
            hostname=host, origin=origin, workos_subject=workos_subject,
            workos_organization_id=workos_organization_id,
            customer_account_id=row[1], client_brand_id=row[2], tenant_id=row[3],
            brand_id=row[4], client_role=row[5],
            approval_scopes=tuple(json.loads(row[6])), session_id=session_id,
            entitlement_version=int(row[7]), correlation_id=correlation_id,
        )

    def revoke_session(self, *, session_id: str, actor_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT m.brand_id FROM sessions s JOIN memberships m USING (membership_id) "
                "WHERE s.session_id = ?", (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            connection.execute(
                "UPDATE sessions SET state = 'revoked', last_seen_at = ? WHERE session_id = ?",
                (utc_now(), session_id),
            )
            self._audit(connection, actor_id, row[0], "revoke_session", session_id, "ALLOW")
            connection.commit()
        finally:
            connection.close()

    def build_request_context(
        self,
        *,
        session_id: str,
        hostname: str,
        origin: str,
        access_identity_verified: bool,
        correlation_id: str,
    ) -> PortalRequestContext:
        host = _normalise_host(hostname)
        expected_origin = f"https://{host}"
        if not access_identity_verified or origin != expected_origin:
            raise FleetPortalAuthorizationError("edge identity and exact origin are required")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT m.workos_subject, m.workos_organization_id,
                       m.customer_account_id, m.client_brand_id, m.tenant_id,
                       m.brand_id, m.client_role, m.scopes_json,
                       m.entitlement_version, s.state, m.state, s.expires_at,
                       s.created_at, s.last_seen_at
                FROM sessions s JOIN memberships m USING (membership_id)
                WHERE s.session_id = ? AND m.hostname = ?
                """,
                (session_id, host),
            ).fetchone()
            now = datetime.now(timezone.utc)
            expired = row is not None and (
                now >= min(_parse_timestamp(row[11]), _parse_timestamp(row[12]) + _SESSION_ABSOLUTE_LIMIT)
                or now - _parse_timestamp(row[13]) > _SESSION_IDLE_LIMIT
            )
            if row is None or row[9] != "active" or row[10] != "active" or expired:
                raise FleetPortalAuthorizationError("session is absent, revoked, expired, or on the wrong host")
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?", (utc_now(), session_id),
            )
            connection.commit()
        finally:
            connection.close()
        return PortalRequestContext(
            hostname=host, origin=origin, workos_subject=row[0],
            workos_organization_id=row[1], customer_account_id=row[2],
            client_brand_id=row[3], tenant_id=row[4], brand_id=row[5],
            client_role=row[6], approval_scopes=tuple(json.loads(row[7])),
            session_id=session_id, entitlement_version=int(row[8]),
            correlation_id=correlation_id,
        )

    def record_source(
        self,
        context: PortalRequestContext,
        *,
        source_id: str,
        inspection: Mapping[str, Any],
        purpose: str,
        consent_basis: str,
        visibility: str,
        sensitivity: str,
    ) -> dict[str, Any]:
        if context.client_role not in {"owner", "approver", "contributor"}:
            raise FleetPortalAuthorizationError("role may not contribute sources")
        metadata = dict(inspection)
        required = {"filename", "detected_type", "size_bytes", "content_checksum", "malware_scan"}
        if not required.issubset(metadata) or metadata["malware_scan"] != "clean":
            raise FleetPortalError("source has no clean admission evidence")
        now = utc_now()
        record = {
            "source_id": source_id, "tenant_id": context.tenant_id,
            "brand_id": context.brand_id, "purpose": purpose,
            "consent_basis": consent_basis, "visibility": visibility,
            "sensitivity": sensitivity, "state": "review_required",
            "inspection": metadata, "created_by": context.workos_subject,
            "created_at": now,
        }
        checksum = payload_checksum(record)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id, context.tenant_id, context.brand_id,
                    canonical_bytes(record).decode("utf-8"), checksum,
                    "review_required", purpose, consent_basis, visibility,
                    sensitivity, now, now,
                ),
            )
            self._audit(connection, context.workos_subject, context.brand_id, "record_source", source_id, "ALLOW")
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise FleetPortalError("source identifier is already in use") from exc
        finally:
            connection.close()
        return {**record, "record_checksum": checksum}

    def create_candidate_fact(
        self,
        context: PortalRequestContext,
        *,
        candidate_id: str,
        source_id: str,
        source_locator: str,
        statement: str,
    ) -> dict[str, Any]:
        connection = self._connect()
        now = utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT checksum FROM sources WHERE source_id = ? AND tenant_id = ? AND brand_id = ?",
                (source_id, context.tenant_id, context.brand_id),
            ).fetchone()
            if source is None:
                raise FleetPortalAuthorizationError("source is not in this tenant")
            candidate = {
                "candidate_id": candidate_id, "source_id": source_id,
                "source_checksum": source[0], "source_locator": source_locator,
                "statement": statement, "status": "client_review",
                "tenant_id": context.tenant_id, "brand_id": context.brand_id,
                "created_at": now,
            }
            checksum = payload_checksum(candidate)
            connection.execute(
                "INSERT INTO fact_candidates VALUES (?, ?, ?, ?, ?, 'client_review', ?, ?)",
                (
                    candidate_id, context.tenant_id, context.brand_id, source_id,
                    canonical_bytes(candidate).decode("utf-8"), checksum, now,
                ),
            )
            self._audit(connection, context.workos_subject, context.brand_id, "create_candidate_fact", candidate_id, "ALLOW")
            connection.commit()
        finally:
            connection.close()
        return {**candidate, "candidate_checksum": checksum}

    def submit_command(
        self,
        context: PortalRequestContext,
        *,
        command_id: str,
        idempotency_key: str,
        command_type: str,
        target_id: str,
        expected_checksum: str,
        approval_scope: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if context.client_role not in {"owner", "approver"}:
            raise FleetPortalAuthorizationError("role may not make this decision")
        if approval_scope not in context.approval_scopes:
            raise FleetPortalAuthorizationError("membership lacks the approval scope")
        body_checksum = payload_checksum(payload)
        now = utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT command_id, payload_checksum, state FROM portal_commands "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (context.tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing[0] != command_id or existing[1] != body_checksum:
                    raise FleetPortalError("idempotency key was reused with different content")
                connection.rollback()
                return self.command_projection(context, command_id=command_id)
            recent_count = connection.execute(
                "SELECT COUNT(*) FROM portal_commands WHERE tenant_id = ? AND actor_id = ? "
                "AND created_at >= ?",
                (
                    context.tenant_id, context.workos_subject,
                    (datetime.now(timezone.utc) - timedelta(minutes=1))
                    .isoformat().replace("+00:00", "Z"),
                ),
            ).fetchone()[0]
            if recent_count >= _COMMAND_RATE_LIMIT_PER_MINUTE:
                raise FleetPortalAuthorizationError("the portal command rate limit was exceeded")
            if command_type == "paperclip_approval_decision":
                binding = connection.execute(
                    "SELECT approval_checksum, state FROM approval_snapshots "
                    "WHERE approval_id = ? AND tenant_id = ? AND brand_id = ?",
                    (target_id, context.tenant_id, context.brand_id),
                ).fetchone()
                if binding is None or binding[1] != "pending" or binding[0] != expected_checksum:
                    raise FleetPortalError("Paperclip approval snapshot is absent, stale, or resolved")
            connection.execute(
                """
                INSERT INTO portal_commands (
                    command_id, idempotency_key, tenant_id, brand_id, actor_id,
                    session_id, command_type, target_id, expected_checksum,
                    approval_scope, payload_json, payload_checksum, correlation_id,
                    state, authority_receipt_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', NULL, ?, ?)
                """,
                (
                    command_id, idempotency_key, context.tenant_id, context.brand_id,
                    context.workos_subject, context.session_id, command_type, target_id,
                    expected_checksum, approval_scope,
                    canonical_bytes(dict(payload)).decode("utf-8"), body_checksum,
                    context.correlation_id, now, now,
                ),
            )
            self._audit(connection, context.workos_subject, context.brand_id, "submit_command", command_id, "ALLOW_RECEIVED")
            connection.commit()
        finally:
            connection.close()
        return self.command_projection(context, command_id=command_id)

    def transition_command(
        self,
        *,
        worker_id: str,
        tenant_id: str,
        brand_id: str,
        command_id: str,
        expected_state: str,
        next_state: str,
        authority_receipt: Mapping[str, Any] | None = None,
    ) -> None:
        if expected_state not in COMMAND_STATES or next_state not in COMMAND_STATES:
            raise FleetPortalError("unknown command state")
        if next_state not in _COMMAND_TRANSITIONS[expected_state]:
            raise FleetPortalError("illegal command transition")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE portal_commands
                SET state = ?, authority_receipt_json = COALESCE(?, authority_receipt_json),
                    updated_at = ?
                WHERE command_id = ? AND tenant_id = ? AND brand_id = ? AND state = ?
                """,
                (
                    next_state,
                    None if authority_receipt is None else canonical_bytes(dict(authority_receipt)).decode("utf-8"),
                    utc_now(), command_id, tenant_id, brand_id, expected_state,
                ),
            )
            if cursor.rowcount != 1:
                raise FleetPortalError("command is absent, cross-tenant, or stale")
            self._audit(connection, worker_id, brand_id, "transition_command", command_id, f"ALLOW_{next_state.upper()}")
            if next_state == "completed":
                connection.execute(
                    "UPDATE approval_snapshots SET state = 'resolved', updated_at = ? "
                    "WHERE approval_id = (SELECT target_id FROM portal_commands WHERE command_id = ?) "
                    "AND tenant_id = ? AND brand_id = ? AND state = 'pending'",
                    (utc_now(), command_id, tenant_id, brand_id),
                )
            connection.commit()
        finally:
            connection.close()

    def claim_next_command(self, *, worker_id: str, brand_id: str) -> dict[str, Any] | None:
        """Atomically claim the oldest received command for one brand."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT command_id, tenant_id, brand_id, command_type, target_id,
                       expected_checksum, approval_scope, payload_json,
                       payload_checksum, correlation_id
                FROM portal_commands
                WHERE brand_id = ? AND state = 'received'
                ORDER BY created_at, command_id LIMIT 1
                """,
                (brand_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            cursor = connection.execute(
                "UPDATE portal_commands SET state = 'dispatching', updated_at = ? "
                "WHERE command_id = ? AND brand_id = ? AND state = 'received'",
                (utc_now(), row[0], brand_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            self._audit(
                connection, worker_id, brand_id, "claim_command", row[0],
                "ALLOW_DISPATCHING",
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "command_id": row[0], "tenant_id": row[1], "brand_id": row[2],
            "command_type": row[3], "target_id": row[4],
            "expected_checksum": row[5], "approval_scope": row[6],
            "payload": json.loads(row[7]), "payload_checksum": row[8],
            "correlation_id": row[9],
        }

    def command_projection(self, context: PortalRequestContext, *, command_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT command_id, command_type, target_id, state, correlation_id,
                       created_at, updated_at
                FROM portal_commands
                WHERE command_id = ? AND tenant_id = ? AND brand_id = ?
                """,
                (command_id, context.tenant_id, context.brand_id),
            ).fetchone()
            if row is None:
                raise KeyError(command_id)
        finally:
            connection.close()
        return {
            "command_id": row[0], "command_type": row[1], "target_id": row[2],
            "state": row[3], "correlation_id": row[4],
            "created_at": row[5], "updated_at": row[6],
        }

    def add_content_item(
        self,
        *,
        actor_id: str,
        content_id: str,
        tenant_id: str,
        brand_id: str,
        title: str,
        content_type: str,
        lifecycle_state: str,
        source_checksum: str,
    ) -> dict[str, Any]:
        item = {
            "content_id": content_id, "tenant_id": tenant_id, "brand_id": brand_id,
            "title": title, "content_type": content_type,
            "lifecycle_state": lifecycle_state, "source_checksum": source_checksum,
            "created_at": utc_now(),
        }
        checksum = payload_checksum(item)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT tenant_id, brand_id, title, content_type, lifecycle_state, "
                "source_checksum, content_checksum, created_at FROM content_catalogue "
                "WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing[:6]) != (
                    tenant_id, brand_id, title, content_type, lifecycle_state, source_checksum,
                ):
                    raise FleetPortalError("content identifier is already bound to different content")
                connection.rollback()
                return {
                    "content_id": content_id, "tenant_id": existing[0], "brand_id": existing[1],
                    "title": existing[2], "content_type": existing[3],
                    "lifecycle_state": existing[4], "source_checksum": existing[5],
                    "content_checksum": existing[6], "created_at": existing[7],
                }
            connection.execute(
                "INSERT INTO content_catalogue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content_id, tenant_id, brand_id, title, content_type,
                    lifecycle_state, source_checksum, checksum, item["created_at"],
                ),
            )
            self._audit(connection, actor_id, brand_id, "add_content_item", content_id, "ALLOW")
            connection.commit()
        finally:
            connection.close()
        return {**item, "content_checksum": checksum}

    def bind_paperclip_approval(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        brand_id: str,
        approval_id: str,
        approval_checksum: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f-]{36}", approval_id) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", approval_checksum
        ):
            raise FleetPortalError("Paperclip approval snapshot identity is invalid")
        now = utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT tenant_id, brand_id, approval_checksum, state FROM approval_snapshots "
                "WHERE approval_id = ?", (approval_id,),
            ).fetchone()
            expected = (tenant_id, brand_id, approval_checksum, "pending")
            if existing is not None and tuple(existing) != expected:
                raise FleetPortalError("Paperclip approval snapshot is immutable or resolved")
            if existing is None:
                connection.execute(
                    "INSERT INTO approval_snapshots VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (approval_id, tenant_id, brand_id, approval_checksum, now, now),
                )
                self._audit(
                    connection, actor_id, brand_id, "bind_paperclip_approval",
                    approval_id, "ALLOW_PENDING",
                )
            connection.commit()
        finally:
            connection.close()
        return {
            "approval_id": approval_id, "tenant_id": tenant_id, "brand_id": brand_id,
            "approval_checksum": approval_checksum, "state": "pending",
        }

    def list_content(self, context: PortalRequestContext) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT content_id, title, content_type, lifecycle_state, source_checksum, content_checksum, created_at "
                "FROM content_catalogue WHERE tenant_id = ? AND brand_id = ? ORDER BY created_at, content_id",
                (context.tenant_id, context.brand_id),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "content_id": row[0], "title": row[1], "content_type": row[2],
                "lifecycle_state": row[3], "source_checksum": row[4],
                "content_checksum": row[5], "created_at": row[6],
            }
            for row in rows
        ]

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
            row = connection.execute("SELECT version FROM portal_schema WHERE id = 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO portal_schema VALUES (1, ?)", (_SCHEMA_VERSION,))
            elif int(row[0]) != _SCHEMA_VERSION:
                raise FleetPortalError("portal authority schema version is unsupported")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise FleetPortalError("portal authority foreign key check failed")
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            validate_sqlite_storage(self.database_path, self._storage_identity)
            connection = sqlite3.connect(self.database_path, timeout=self.timeout_seconds)
            validate_sqlite_storage(self.database_path, self._storage_identity)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            raise FleetPortalError("could not open portal authority") from exc

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        actor_id: str,
        brand_id: str,
        operation: str,
        target_id: str,
        outcome: str,
    ) -> None:
        connection.execute(
            "INSERT INTO portal_audit (actor_id, brand_id, operation, target_id, outcome, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (actor_id, brand_id, operation, target_id, outcome, utc_now()),
        )


def _normalise_host(hostname: str) -> str:
    if not isinstance(hostname, str) or ":" in hostname or "/" in hostname:
        raise FleetPortalAuthorizationError("hostname is invalid")
    host = hostname.strip().lower().rstrip(".")
    if not _HOST.fullmatch(host):
        raise FleetPortalAuthorizationError("hostname is not an approved Fleet host shape")
    return host


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise FleetPortalAuthorizationError("session timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise FleetPortalAuthorizationError("session timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_schema (
    id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    membership_id TEXT PRIMARY KEY, workos_subject TEXT NOT NULL,
    workos_organization_id TEXT NOT NULL, customer_account_id TEXT NOT NULL,
    client_brand_id TEXT NOT NULL, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    client_role TEXT NOT NULL, scopes_json TEXT NOT NULL, hostname TEXT NOT NULL,
    entitlement_version INTEGER NOT NULL, state TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (workos_subject, workos_organization_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, membership_id TEXT NOT NULL,
    workos_subject TEXT NOT NULL, workos_organization_id TEXT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (membership_id) REFERENCES memberships(membership_id)
);
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    record_json TEXT NOT NULL, checksum TEXT NOT NULL, state TEXT NOT NULL,
    purpose TEXT NOT NULL, consent_basis TEXT NOT NULL, visibility TEXT NOT NULL,
    sensitivity TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_candidates (
    candidate_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    source_id TEXT NOT NULL, record_json TEXT NOT NULL, checksum TEXT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);
CREATE TABLE IF NOT EXISTS portal_commands (
    command_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
    tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    session_id TEXT NOT NULL, command_type TEXT NOT NULL, target_id TEXT NOT NULL,
    expected_checksum TEXT NOT NULL, approval_scope TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_checksum TEXT NOT NULL,
    correlation_id TEXT NOT NULL, state TEXT NOT NULL,
    authority_receipt_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS approval_snapshots (
    approval_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    approval_checksum TEXT NOT NULL, state TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_catalogue (
    content_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    title TEXT NOT NULL, content_type TEXT NOT NULL, lifecycle_state TEXT NOT NULL,
    source_checksum TEXT NOT NULL, content_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portal_audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL,
    brand_id TEXT NOT NULL, operation TEXT NOT NULL, target_id TEXT NOT NULL,
    outcome TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS portal_commands_tenant_state
    ON portal_commands (tenant_id, brand_id, state, created_at);
CREATE INDEX IF NOT EXISTS content_catalogue_tenant
    ON content_catalogue (tenant_id, brand_id, created_at);
"""
