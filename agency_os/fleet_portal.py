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

from .brand_intelligence import BrandIntelligenceAuthority, BrandIntelligenceError
from .contracts import canonical_bytes, utc_now
from .fleet_tenancy import (
    FleetTenantAuthority,
    FleetTenancyAuthorizationError,
    FleetTenancyError,
)
from .sqlite_storage import SQLiteStorageError, prepare_sqlite_storage, validate_sqlite_storage
from .store import Principal


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
_SCHEMA_VERSION = 3
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
                    members = archive.infolist()
                    if len(members) > 500:
                        raise FleetPortalError("Office document has too many archive members")
                    total_uncompressed = 0
                    for member in members:
                        if member.flag_bits & 0x1:
                            raise FleetPortalError("encrypted Office documents are not admitted")
                        member_path = Path(member.filename)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            raise FleetPortalError("Office document member path is unsafe")
                        total_uncompressed += member.file_size
                        if member.file_size > 20 * 1024 * 1024:
                            raise FleetPortalError("Office document member is too large")
                        if member.compress_size == 0 and member.file_size > 0:
                            raise FleetPortalError("Office document compression ratio is unsafe")
                        if member.compress_size and member.file_size / member.compress_size > 100:
                            raise FleetPortalError("Office document compression ratio is unsafe")
                    if total_uncompressed > 100 * 1024 * 1024:
                        raise FleetPortalError("Office document expands beyond the admitted limit")
                    names = {member.filename for member in members}
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

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        tenant_authority_path: str | os.PathLike[str] | None = None,
        brand_intelligence_path: str | os.PathLike[str] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.database_path = Path(database_path)
        self.tenant_authority_path = (
            None if tenant_authority_path is None else Path(tenant_authority_path)
        )
        self.brand_intelligence_path = (
            None if brand_intelligence_path is None else Path(brand_intelligence_path)
        )
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
            rows = connection.execute(
                """
                SELECT membership_id, customer_account_id, client_brand_id, tenant_id, brand_id,
                       client_role, scopes_json, entitlement_version, state
                FROM memberships
                WHERE workos_subject = ? AND workos_organization_id = ? AND hostname = ?
                """,
                (workos_subject, workos_organization_id, host),
            ).fetchall()
            row = rows[0] if len(rows) == 1 else None
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
        context = PortalRequestContext(
            hostname=host, origin=origin, workos_subject=workos_subject,
            workos_organization_id=workos_organization_id,
            customer_account_id=row[1], client_brand_id=row[2], tenant_id=row[3],
            brand_id=row[4], client_role=row[5],
            approval_scopes=tuple(json.loads(row[6])), session_id=session_id,
            entitlement_version=int(row[7]), correlation_id=correlation_id,
        )
        self._enforce_authoritative_access(context)
        return context

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

    def revoke_membership(self, *, membership_id: str, actor_id: str) -> None:
        """Revoke membership and every active session in one transaction."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT brand_id, state FROM memberships WHERE membership_id = ?",
                (membership_id,),
            ).fetchone()
            if row is None:
                raise KeyError(membership_id)
            now = utc_now()
            connection.execute(
                "UPDATE memberships SET state = 'revoked', updated_at = ? "
                "WHERE membership_id = ?",
                (now, membership_id),
            )
            connection.execute(
                "UPDATE sessions SET state = 'revoked', last_seen_at = ? "
                "WHERE membership_id = ? AND state = 'active'",
                (now, membership_id),
            )
            self._audit(
                connection, actor_id, row[0], "revoke_membership",
                membership_id, "ALLOW",
            )
            connection.commit()
        finally:
            connection.close()

    def issue_invitation(
        self,
        *,
        actor_id: str,
        invitation_id: str,
        invitation_token: str,
        email: str,
        workos_organization_id: str,
        tenant_id: str,
        brand_id: str,
        client_role: str,
        approval_scopes: Iterable[str],
        hostname: str,
    ) -> dict[str, Any]:
        role = client_role.lower()
        scopes = tuple(sorted(set(approval_scopes)))
        host = _normalise_host(hostname)
        if role not in CLIENT_ROLES or any(scope not in APPROVAL_SCOPES for scope in scopes):
            raise FleetPortalAuthorizationError("invitation role or scope is invalid")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.lower()):
            raise FleetPortalError("invitation email is invalid")
        if len(invitation_token) < 32:
            raise FleetPortalError("invitation token is too short")
        token_hash = hashlib.sha256(invitation_token.encode("utf-8")).hexdigest()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expires_at = (now_dt + timedelta(hours=72)).isoformat().replace("+00:00", "Z")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT token_hash, tenant_id, brand_id, email, state FROM invitations "
                "WHERE invitation_id = ?",
                (invitation_id,),
            ).fetchone()
            expected = (token_hash, tenant_id, brand_id, email.lower(), "pending")
            if existing is not None and tuple(existing) != expected:
                raise FleetPortalError("invitation identity is already bound")
            if existing is None:
                connection.execute(
                    "INSERT INTO invitations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (
                        invitation_id, token_hash, email.lower(), workos_organization_id,
                        tenant_id, brand_id, role, json.dumps(scopes), host,
                        expires_at, now, now,
                    ),
                )
            self._audit(
                connection, actor_id, brand_id, "issue_invitation",
                invitation_id, "ALLOW_PENDING",
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "invitation_id": invitation_id, "email": email.lower(),
            "tenant_id": tenant_id, "brand_id": brand_id, "state": "pending",
            "expires_at": expires_at,
        }

    def issue_invitation_for_context(
        self,
        context: PortalRequestContext,
        *,
        invitation_id: str,
        invitation_token: str,
        email: str,
        client_role: str,
        approval_scopes: Iterable[str],
    ) -> dict[str, Any]:
        if context.client_role != "owner" or "access_change" not in context.approval_scopes:
            raise FleetPortalAuthorizationError("owner access-change authority is required")
        return self.issue_invitation(
            actor_id=context.workos_subject, invitation_id=invitation_id,
            invitation_token=invitation_token, email=email,
            workos_organization_id=context.workos_organization_id,
            tenant_id=context.tenant_id, brand_id=context.brand_id,
            client_role=client_role, approval_scopes=approval_scopes,
            hostname=context.hostname,
        )

    def pending_invitation_projection(self, *, invitation_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT email, workos_organization_id, tenant_id, brand_id, hostname, "
                "client_role, scopes_json, state, expires_at FROM invitations "
                "WHERE invitation_id = ?",
                (invitation_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(invitation_id)
        return {
            "invitation_id": invitation_id, "email": row[0],
            "workos_organization_id": row[1], "tenant_id": row[2],
            "brand_id": row[3], "hostname": row[4], "client_role": row[5],
            "approval_scopes": json.loads(row[6]), "state": row[7],
            "expires_at": row[8],
        }

    def accept_invitation(
        self,
        *,
        invitation_id: str,
        invitation_token: str,
        invited_email: str,
        verified_hostname: str,
        membership_id: str,
        workos_subject: str,
        workos_organization_id: str,
        customer_account_id: str,
        client_brand_id: str,
        entitlement_version: int,
    ) -> None:
        """Atomically exchange one email-bound invitation for one membership."""

        if entitlement_version < 1:
            raise FleetPortalError("entitlement version must be positive")
        email = invited_email.strip().lower()
        host = _normalise_host(verified_hostname)
        token_hash = hashlib.sha256(invitation_token.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT token_hash, email, workos_organization_id, tenant_id, brand_id, "
                "client_role, scopes_json, hostname, state, expires_at "
                "FROM invitations WHERE invitation_id = ?",
                (invitation_id,),
            ).fetchone()
            if (
                row is None
                or row[8] != "pending"
                or row[0] != token_hash
                or row[1] != email
                or row[2] != workos_organization_id
                or row[7] != host
                or datetime.now(timezone.utc) >= _parse_timestamp(row[9])
            ):
                raise FleetPortalAuthorizationError(
                    "invitation is absent, expired or does not match this identity"
                )
            now = utc_now()
            record = (
                membership_id, workos_subject, workos_organization_id,
                customer_account_id, client_brand_id, row[3], row[4], row[5],
                row[6], row[7], entitlement_version, "active", now, now,
            )
            existing = connection.execute(
                "SELECT workos_subject, workos_organization_id, customer_account_id, "
                "client_brand_id, tenant_id, brand_id, client_role, scopes_json, "
                "hostname, entitlement_version, state FROM memberships "
                "WHERE membership_id = ?",
                (membership_id,),
            ).fetchone()
            if existing is not None and tuple(existing) != record[1:12]:
                raise FleetPortalError("membership identity is already bound")
            if existing is None:
                connection.execute(
                    "INSERT INTO memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    record,
                )
            cursor = connection.execute(
                "UPDATE invitations SET state = 'accepted', updated_at = ? "
                "WHERE invitation_id = ? AND token_hash = ? AND state = 'pending'",
                (now, invitation_id, token_hash),
            )
            if cursor.rowcount != 1:
                raise FleetPortalError("invitation acceptance raced with another operation")
            self._audit(
                connection, workos_subject, row[4], "accept_invitation",
                invitation_id, "ALLOW",
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise FleetPortalError("invitation conflicts with an existing identity") from exc
        finally:
            connection.close()

    def revoke_membership_for_context(
        self, context: PortalRequestContext, *, membership_id: str,
    ) -> None:
        if context.client_role != "owner" or "access_change" not in context.approval_scopes:
            raise FleetPortalAuthorizationError("owner access-change authority is required")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT workos_subject, tenant_id, brand_id, state FROM memberships "
                "WHERE membership_id = ?",
                (membership_id,),
            ).fetchone()
        finally:
            connection.close()
        if (
            row is None or row[1] != context.tenant_id or row[2] != context.brand_id
            or row[3] != "active" or row[0] == context.workos_subject
        ):
            raise FleetPortalAuthorizationError(
                "membership is absent, cross-tenant, inactive or belongs to the acting owner"
            )
        self.revoke_membership(membership_id=membership_id, actor_id=context.workos_subject)

    def list_invitations(self, context: PortalRequestContext) -> list[dict[str, Any]]:
        if context.client_role != "owner":
            return []
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT invitation_id, email, client_role, scopes_json, state, expires_at, created_at "
                "FROM invitations WHERE tenant_id = ? AND brand_id = ? "
                "ORDER BY created_at DESC, invitation_id",
                (context.tenant_id, context.brand_id),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "invitation_id": row[0], "email": row[1], "client_role": row[2],
                "approval_scopes": json.loads(row[3]), "state": row[4],
                "expires_at": row[5], "created_at": row[6],
            }
            for row in rows
        ]

    def list_memberships(self, context: PortalRequestContext) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT membership_id, workos_subject, client_role, scopes_json, state, updated_at "
                "FROM memberships WHERE tenant_id = ? AND brand_id = ? ORDER BY created_at",
                (context.tenant_id, context.brand_id),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "membership_id": row[0], "workos_subject": row[1],
                "client_role": row[2], "approval_scopes": json.loads(row[3]),
                "state": row[4], "updated_at": row[5],
            }
            for row in rows
        ]

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
        context = PortalRequestContext(
            hostname=host, origin=origin, workos_subject=row[0],
            workos_organization_id=row[1], customer_account_id=row[2],
            client_brand_id=row[3], tenant_id=row[4], brand_id=row[5],
            client_role=row[6], approval_scopes=tuple(json.loads(row[7])),
            session_id=session_id, entitlement_version=int(row[8]),
            correlation_id=correlation_id,
        )
        self._enforce_authoritative_access(context)
        return context

    def reserve_source_upload(
        self,
        context: PortalRequestContext,
        *,
        source_id: str,
        filename: str,
        size_bytes: int,
        purpose: str,
    ) -> dict[str, Any]:
        """Reserve bounded Launch Room capacity before the web tier writes bytes."""

        self._enforce_authoritative_access(context)
        if context.client_role not in {"owner", "approver", "contributor"}:
            raise FleetPortalAuthorizationError("role may not supply Launch Room sources")
        if not re.fullmatch(r"source_[A-Za-z0-9_-]{1,96}", source_id):
            raise FleetPortalError("source identity is invalid")
        safe_name = Path(filename).name
        if safe_name != filename or size_bytes < 1 or size_bytes > SourceAdmissionPolicy.maximum_file_bytes:
            raise FleetPortalError("upload reservation is invalid")
        normalized_purpose = " ".join(purpose.split())
        if not normalized_purpose or len(normalized_purpose) > 500:
            raise FleetPortalError("upload purpose is invalid")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        hour_ago = (now_dt - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE upload_reservations SET state = 'expired', updated_at = ? "
                "WHERE state = 'reserved' AND created_at < ?",
                (now, hour_ago),
            )
            existing = connection.execute(
                "SELECT tenant_id, brand_id, actor_id, filename, size_bytes, purpose, state, created_at "
                "FROM upload_reservations WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if existing is not None:
                expected = (
                    context.tenant_id, context.brand_id, context.workos_subject,
                    safe_name, size_bytes, normalized_purpose,
                )
                if tuple(existing[:6]) != expected or existing[6] not in {"reserved", "processed"}:
                    raise FleetPortalError("source reservation identity is already bound")
                connection.rollback()
                return {
                    "source_id": source_id, "state": existing[6],
                    "size_bytes": size_bytes, "created_at": existing[7],
                }
            recent = connection.execute(
                "SELECT COUNT(*) FROM upload_reservations WHERE tenant_id = ? AND brand_id = ? "
                "AND actor_id = ? AND created_at >= ? AND state != 'cancelled'",
                (context.tenant_id, context.brand_id, context.workos_subject, hour_ago),
            ).fetchone()[0]
            if recent >= 20:
                raise FleetPortalAuthorizationError("Launch Room upload rate limit was exceeded")
            total = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM upload_reservations "
                "WHERE tenant_id = ? AND brand_id = ? AND state IN ('reserved', 'processed')",
                (context.tenant_id, context.brand_id),
            ).fetchone()[0]
            if total + size_bytes > SourceAdmissionPolicy.maximum_case_bytes:
                raise FleetPortalError("Launch Room upload capacity would be exceeded")
            connection.execute(
                "INSERT INTO upload_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)",
                (
                    f"reservation_{source_id.removeprefix('source_')}", source_id,
                    context.tenant_id, context.brand_id, context.workos_subject,
                    safe_name, size_bytes, normalized_purpose, now, now,
                ),
            )
            self._audit(
                connection, context.workos_subject, context.brand_id,
                "reserve_source_upload", source_id, "ALLOW_RESERVED",
            )
            connection.commit()
        finally:
            connection.close()
        return {"source_id": source_id, "state": "reserved", "size_bytes": size_bytes, "created_at": now}

    def cancel_source_upload(
        self, context: PortalRequestContext, *, source_id: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE upload_reservations SET state = 'cancelled', updated_at = ? "
                "WHERE source_id = ? AND tenant_id = ? AND brand_id = ? AND actor_id = ? "
                "AND state = 'reserved'",
                (
                    utc_now(), source_id, context.tenant_id, context.brand_id,
                    context.workos_subject,
                ),
            )
            if cursor.rowcount != 1:
                raise FleetPortalError("upload reservation is absent or no longer cancellable")
            self._audit(
                connection, context.workos_subject, context.brand_id,
                "cancel_source_upload", source_id, "ALLOW_CANCELLED",
            )
            connection.commit()
        finally:
            connection.close()

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
                "INSERT INTO fact_candidates VALUES (?, ?, ?, ?, ?, ?, 'client_review', ?)",
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

    def import_extraction(self, extraction: Mapping[str, Any]) -> dict[str, Any]:
        """Admit one scanner result and create its reviewable fact candidate."""

        record = dict(extraction)
        supplied_checksum = record.pop("record_checksum", None)
        if supplied_checksum != payload_checksum(record):
            raise FleetPortalError("extraction record checksum is invalid")
        source_id = str(record.get("source_id", ""))
        if not re.fullmatch(r"source_[A-Za-z0-9_-]{1,96}", source_id):
            raise FleetPortalError("source identity is invalid")
        if record.get("state") != "review_required" or not isinstance(
            record.get("extracted_text"), str
        ):
            raise FleetPortalError("extraction is not reviewable")
        tenant_id = str(record.get("tenant_id", ""))
        brand_id = str(record.get("brand_id", ""))
        submitted_by = str(record.get("submitted_by", ""))
        inspection = record.get("inspection")
        if not all((tenant_id, brand_id, submitted_by)) or not isinstance(inspection, Mapping):
            raise FleetPortalError("extraction tenant or inspection is incomplete")
        size_bytes = inspection.get("size_bytes")
        if not isinstance(size_bytes, int) or size_bytes < 1:
            raise FleetPortalError("extraction size evidence is invalid")
        source_record = {
            "source_id": source_id, "tenant_id": tenant_id, "brand_id": brand_id,
            "purpose": str(record.get("purpose", "")),
            "consent_basis": str(record.get("consent_basis", "")),
            "visibility": "client_and_fleet", "sensitivity": "internal",
            "state": "review_required", "inspection": dict(inspection),
            "created_by": submitted_by,
            "created_at": str(record.get("extracted_at", utc_now())),
        }
        source_checksum = payload_checksum(source_record)
        statement = " ".join(record["extracted_text"].split())[:2000]
        if len(statement) < 3:
            raise FleetPortalError("extraction has no candidate statement")
        candidate_id = "candidate_" + hashlib.sha256(
            f"{tenant_id}:{brand_id}:{source_id}:{source_checksum}".encode("utf-8")
        ).hexdigest()[:32]
        candidate = {
            "candidate_id": candidate_id, "source_id": source_id,
            "source_checksum": source_checksum, "source_locator": "extracted_text:1",
            "statement": statement, "status": "client_review",
            "tenant_id": tenant_id, "brand_id": brand_id,
            "created_at": str(record.get("extracted_at", utc_now())),
        }
        candidate_checksum = payload_checksum(candidate)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT tenant_id, brand_id, actor_id, size_bytes, state "
                "FROM upload_reservations WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if self.tenant_authority_path is not None and (
                reservation is None
                or tuple(reservation[:4]) != (tenant_id, brand_id, submitted_by, size_bytes)
                or reservation[4] not in {"reserved", "processed"}
            ):
                raise FleetPortalError("extraction has no exact authoritative upload reservation")
            existing_source = connection.execute(
                "SELECT checksum FROM sources WHERE source_id = ?", (source_id,),
            ).fetchone()
            if existing_source is not None and existing_source[0] != source_checksum:
                raise FleetPortalError("source extraction conflicts with admitted evidence")
            if existing_source is None:
                connection.execute(
                    "INSERT INTO sources VALUES (?, ?, ?, ?, ?, 'review_required', ?, ?, ?, ?, ?, ?)",
                    (
                        source_id, tenant_id, brand_id,
                        canonical_bytes(source_record).decode("utf-8"), source_checksum,
                        source_record["purpose"], source_record["consent_basis"],
                        source_record["visibility"], source_record["sensitivity"],
                        source_record["created_at"], source_record["created_at"],
                    ),
                )
            existing_candidate = connection.execute(
                "SELECT checksum FROM fact_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing_candidate is not None and existing_candidate[0] != candidate_checksum:
                raise FleetPortalError("candidate identity conflicts with admitted evidence")
            if existing_candidate is None:
                connection.execute(
                    "INSERT INTO fact_candidates VALUES (?, ?, ?, ?, ?, ?, 'client_review', ?)",
                    (
                        candidate_id, tenant_id, brand_id, source_id,
                        canonical_bytes(candidate).decode("utf-8"),
                        candidate_checksum, candidate["created_at"],
                    ),
                )
            if reservation is not None:
                connection.execute(
                    "UPDATE upload_reservations SET state = 'processed', updated_at = ? "
                    "WHERE source_id = ? AND state = 'reserved'",
                    (utc_now(), source_id),
                )
            self._audit(
                connection, "fleet-ingest-worker", brand_id,
                "import_extraction", source_id, "ALLOW_REVIEW_REQUIRED",
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "source_id": source_id, "source_checksum": source_checksum,
            "candidate_id": candidate_id, "candidate_checksum": candidate_checksum,
            "state": "client_review",
        }

    def list_sources(self, context: PortalRequestContext) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT record_json, checksum, state, updated_at FROM sources "
                "WHERE tenant_id = ? AND brand_id = ? ORDER BY created_at",
                (context.tenant_id, context.brand_id),
            ).fetchall()
        finally:
            connection.close()
        return [
            {**json.loads(row[0]), "record_checksum": row[1], "state": row[2], "updated_at": row[3]}
            for row in rows
        ]

    def list_candidates(self, context: PortalRequestContext) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT c.record_json, c.checksum, c.state, r.review_checksum,
                       r.statement, r.reviewed_by, r.reviewed_at
                FROM fact_candidates c
                LEFT JOIN candidate_reviews r ON r.candidate_id = c.candidate_id
                WHERE c.tenant_id = ? AND c.brand_id = ?
                ORDER BY c.created_at, c.candidate_id
                """,
                (context.tenant_id, context.brand_id),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                **json.loads(row[0]), "candidate_checksum": row[1],
                "state": row[2], "review_checksum": row[3],
                "reviewed_statement": row[4], "reviewed_by": row[5],
                "reviewed_at": row[6],
            }
            for row in rows
        ]

    def confirm_candidate(
        self,
        context: PortalRequestContext,
        *,
        candidate_id: str,
        expected_checksum: str,
        statement: str,
    ) -> dict[str, Any]:
        if context.client_role not in {"owner", "approver"}:
            raise FleetPortalAuthorizationError("role may not confirm brand facts")
        corrected = " ".join(statement.split())
        if len(corrected) < 3 or len(corrected) > 2000:
            raise FleetPortalError("candidate statement length is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT source_id, checksum, state FROM fact_candidates "
                "WHERE candidate_id = ? AND tenant_id = ? AND brand_id = ?",
                (candidate_id, context.tenant_id, context.brand_id),
            ).fetchone()
            if row is None or row[1] != expected_checksum or row[2] != "client_review":
                raise FleetPortalError("candidate is absent, stale or already reviewed")
            review = {
                "candidate_id": candidate_id, "tenant_id": context.tenant_id,
                "brand_id": context.brand_id, "source_id": row[0],
                "candidate_checksum": expected_checksum, "statement": corrected,
                "decision": "confirmed", "reviewed_by": context.workos_subject,
                "reviewed_at": utc_now(),
            }
            review_checksum = payload_checksum(review)
            connection.execute(
                "INSERT INTO candidate_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate_id, context.tenant_id, context.brand_id, row[0],
                    expected_checksum, corrected, context.workos_subject,
                    review["reviewed_at"], review_checksum, "confirmed",
                ),
            )
            connection.execute(
                "UPDATE fact_candidates SET state = 'client_confirmed' WHERE candidate_id = ?",
                (candidate_id,),
            )
            self._audit(
                connection, context.workos_subject, context.brand_id,
                "confirm_candidate", candidate_id, "ALLOW_CLIENT_CONFIRMED",
            )
            connection.commit()
        finally:
            connection.close()
        return {**review, "review_checksum": review_checksum, "state": "client_confirmed"}

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
                       payload_checksum, correlation_id, idempotency_key, state
                FROM portal_commands
                WHERE brand_id = ? AND state IN ('received', 'unknown')
                ORDER BY CASE state WHEN 'unknown' THEN 0 ELSE 1 END,
                         created_at, command_id LIMIT 1
                """,
                (brand_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            cursor = connection.execute(
                "UPDATE portal_commands SET state = 'dispatching', updated_at = ? "
                "WHERE command_id = ? AND brand_id = ? AND state = ?",
                (utc_now(), row[0], brand_id, row[11]),
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
            "correlation_id": row[9], "idempotency_key": row[10],
            "claimed_from_state": row[11],
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
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_checksum) or source_checksum == "sha256:" + "0" * 64:
            raise FleetPortalError("content source checksum is invalid or fabricated")
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

    def fleet_review_packet(
        self, *, actor_id: str, candidate_id: str,
    ) -> dict[str, Any]:
        """Return and audit the exact confirmed evidence Fleet may send to Paperclip."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT c.tenant_id, c.brand_id, c.source_id, c.checksum, c.state,
                       r.statement, r.review_checksum, r.reviewed_by, r.reviewed_at,
                       s.checksum, s.record_json
                FROM fact_candidates c
                JOIN candidate_reviews r ON r.candidate_id = c.candidate_id
                JOIN sources s ON s.source_id = c.source_id
                WHERE c.candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if row is None or row[4] != "client_confirmed":
                raise FleetPortalError("candidate is absent or not ready for Fleet review")
            existing = connection.execute(
                "SELECT approval_id FROM approval_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            packet = {
                "schema_version": "1.0",
                "artifact_type": "fleet_portal_brand_fact_decision",
                "tenant_id": row[0], "brand_id": row[1],
                "candidate_id": candidate_id, "candidate_checksum": row[3],
                "source_id": row[2], "source_checksum": row[9],
                "source": json.loads(row[10]), "statement": row[5],
                "review_checksum": row[6], "reviewed_by": row[7],
                "reviewed_at": row[8],
            }
            self._audit(
                connection, actor_id, row[1], "fleet_review_packet",
                candidate_id, "ALLOW_EXACT_EVIDENCE",
            )
            connection.commit()
        finally:
            connection.close()
        return {
            **packet, "packet_checksum": payload_checksum(packet),
            "existing_approval_id": None if existing is None else existing[0],
        }

    def bind_paperclip_approval(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        brand_id: str,
        approval_id: str,
        approval_checksum: str,
        candidate_id: str | None = None,
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
            if candidate_id is not None:
                candidate = connection.execute(
                    "SELECT state FROM fact_candidates WHERE candidate_id = ? "
                    "AND tenant_id = ? AND brand_id = ?",
                    (candidate_id, tenant_id, brand_id),
                ).fetchone()
                if candidate is None or candidate[0] != "client_confirmed":
                    raise FleetPortalError("approval candidate is absent or not client-confirmed")
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
            if candidate_id is not None:
                linked = connection.execute(
                    "SELECT candidate_id FROM approval_candidates WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                if linked is not None and linked[0] != candidate_id:
                    raise FleetPortalError("approval is already bound to another candidate")
                connection.execute(
                    "INSERT OR IGNORE INTO approval_candidates VALUES (?, ?)",
                    (approval_id, candidate_id),
                )
            connection.commit()
        finally:
            connection.close()
        return {
            "approval_id": approval_id, "tenant_id": tenant_id, "brand_id": brand_id,
            "approval_checksum": approval_checksum, "state": "pending",
        }

    def list_approvals(self, context: PortalRequestContext) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT a.approval_id, a.approval_checksum, a.state,
                       c.candidate_id, r.statement, r.review_checksum,
                       s.record_json, a.updated_at
                FROM approval_snapshots a
                LEFT JOIN approval_candidates ac ON ac.approval_id = a.approval_id
                LEFT JOIN fact_candidates c ON c.candidate_id = ac.candidate_id
                LEFT JOIN candidate_reviews r ON r.candidate_id = c.candidate_id
                LEFT JOIN sources s ON s.source_id = c.source_id
                WHERE a.tenant_id = ? AND a.brand_id = ?
                ORDER BY a.created_at, a.approval_id
                """,
                (context.tenant_id, context.brand_id),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "approval_id": row[0], "approval_checksum": row[1],
                "state": row[2], "candidate_id": row[3],
                "statement": row[4], "review_checksum": row[5],
                "source": None if row[6] is None else json.loads(row[6]),
                "updated_at": row[7],
            }
            for row in rows
        ]

    def materialize_approval_outcome(
        self,
        *,
        worker_id: str,
        command_id: str,
        approval: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create an immutable Brand Twin projection after Paperclip readback."""

        approval_id = str(approval.get("id", ""))
        status = str(approval.get("status", ""))
        if status not in {"approved", "rejected"}:
            raise FleetPortalError("Paperclip approval readback is not terminal")
        decision_checksum = payload_checksum(dict(approval))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT p.tenant_id, p.brand_id, p.target_id, p.payload_json,
                       a.approval_checksum, a.state, ac.candidate_id,
                       r.statement, r.review_checksum, c.source_id,
                       s.checksum
                FROM portal_commands p
                JOIN approval_snapshots a ON a.approval_id = p.target_id
                LEFT JOIN approval_candidates ac ON ac.approval_id = a.approval_id
                LEFT JOIN candidate_reviews r ON r.candidate_id = ac.candidate_id
                LEFT JOIN fact_candidates c ON c.candidate_id = ac.candidate_id
                LEFT JOIN sources s ON s.source_id = c.source_id
                WHERE p.command_id = ?
                """,
                (command_id,),
            ).fetchone()
            if row is None or row[2] != approval_id or row[5] != "pending":
                raise FleetPortalError("command approval binding is absent or already resolved")
            payload = json.loads(row[3])
            expected_status = "approved" if payload.get("decision") == "approve" else "rejected"
            if status != expected_status:
                raise FleetPortalError("Paperclip readback conflicts with the submitted decision")
            result: dict[str, Any] = {
                "approval_id": approval_id, "status": status,
                "decision_checksum": decision_checksum,
            }
            existing_twin = connection.execute(
                "SELECT version_id, version, checksum, approval_decision_checksum "
                "FROM brand_twin_versions WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if existing_twin is not None:
                if status != "approved" or existing_twin[3] != decision_checksum:
                    raise FleetPortalError(
                        "existing Brand Twin conflicts with Paperclip decision readback"
                    )
                connection.rollback()
                return {
                    **result,
                    "brand_twin_version_id": existing_twin[0],
                    "brand_twin_version": existing_twin[1],
                    "brand_twin_checksum": existing_twin[2],
                }
            if status == "approved":
                if any(value is None for value in row[6:11]):
                    raise FleetPortalError("approved fact has no complete candidate evidence")
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM brand_twin_versions "
                    "WHERE tenant_id = ? AND brand_id = ?",
                    (row[0], row[1]),
                ).fetchone()[0]
                version_id = f"brand_twin_{row[1]}_v{version}"
                twin = {
                    "schema_version": "1.0", "artifact_type": "brand_twin_fact_version",
                    "version_id": version_id, "version": version,
                    "tenant_id": row[0], "brand_id": row[1],
                    "candidate_id": row[6], "statement": row[7],
                    "review_checksum": row[8], "source_id": row[9],
                    "source_checksum": row[10], "paperclip_approval_id": approval_id,
                    "approval_snapshot_checksum": row[4],
                    "approval_decision_checksum": decision_checksum,
                    "created_at": utc_now(),
                }
                twin_checksum = payload_checksum(twin)
                connection.execute(
                    "INSERT INTO brand_twin_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        version_id, row[0], row[1], version, row[6], row[9],
                        approval_id, canonical_bytes(twin).decode("utf-8"),
                        twin_checksum, row[10], row[4], decision_checksum,
                        twin["created_at"],
                    ),
                )
                connection.execute(
                    "UPDATE fact_candidates SET state = 'approved' WHERE candidate_id = ?",
                    (row[6],),
                )
                result.update({
                    "brand_twin_version_id": version_id,
                    "brand_twin_version": version,
                    "brand_twin_checksum": twin_checksum,
                })
            elif row[6] is not None:
                connection.execute(
                    "UPDATE fact_candidates SET state = 'rejected' WHERE candidate_id = ?",
                    (row[6],),
                )
            self._audit(
                connection, worker_id, row[1], "materialize_approval_outcome",
                approval_id, f"ALLOW_{status.upper()}",
            )
            connection.commit()
        finally:
            connection.close()
        return result

    def admin_projection(self, *, brand_id: str) -> dict[str, Any]:
        """Return the Fleet-only operational view without private Paperclip IDs."""

        connection = self._connect()
        try:
            portal_counts = {
                "memberships": connection.execute(
                    "SELECT COUNT(*) FROM memberships WHERE brand_id = ?", (brand_id,),
                ).fetchone()[0],
                "active_memberships": connection.execute(
                    "SELECT COUNT(*) FROM memberships WHERE brand_id = ? AND state = 'active'",
                    (brand_id,),
                ).fetchone()[0],
                "sources": connection.execute(
                    "SELECT COUNT(*) FROM sources WHERE brand_id = ?", (brand_id,),
                ).fetchone()[0],
                "pending_candidates": connection.execute(
                    "SELECT COUNT(*) FROM fact_candidates WHERE brand_id = ? "
                    "AND state IN ('client_review', 'client_confirmed')", (brand_id,),
                ).fetchone()[0],
                "pending_approvals": connection.execute(
                    "SELECT COUNT(*) FROM approval_snapshots WHERE brand_id = ? AND state = 'pending'",
                    (brand_id,),
                ).fetchone()[0],
                "unknown_commands": connection.execute(
                    "SELECT COUNT(*) FROM portal_commands WHERE brand_id = ? AND state = 'unknown'",
                    (brand_id,),
                ).fetchone()[0],
                "content_items": connection.execute(
                    "SELECT COUNT(*) FROM content_catalogue WHERE brand_id = ?", (brand_id,),
                ).fetchone()[0],
            }
            audit = [
                {
                    "sequence": row[0], "actor_id": row[1], "operation": row[2],
                    "target_id": row[3], "outcome": row[4], "recorded_at": row[5],
                }
                for row in connection.execute(
                    "SELECT sequence, actor_id, operation, target_id, outcome, recorded_at "
                    "FROM portal_audit WHERE brand_id = ? ORDER BY sequence DESC LIMIT 50",
                    (brand_id,),
                ).fetchall()
            ]
        finally:
            connection.close()
        tenant: dict[str, Any] | None = None
        provisioning: dict[str, Any] | None = None
        if self.tenant_authority_path is not None:
            authority = FleetTenantAuthority(self.tenant_authority_path)
            principal = Principal(
                "fleet-portal-admin", "platform-assurance-reviewer", brand_id,
            )
            tenant = authority.account_brand_projection(principal)
            connection = authority._connect()
            try:
                row = connection.execute(
                    "SELECT provisioning_run_id FROM provisioning_runs "
                    "WHERE brand_id = ? ORDER BY created_at DESC LIMIT 1",
                    (brand_id,),
                ).fetchone()
            finally:
                connection.close()
            if row is not None:
                provisioning = authority.provisioning_projection(principal, row[0])
        return {
            "brand_id": brand_id, "tenant": tenant,
            "provisioning": provisioning, "portal_counts": portal_counts,
            "audit": audit,
        }

    def portal_projection(self, context: PortalRequestContext) -> dict[str, Any]:
        authority_projection = self._enforce_authoritative_access(context)
        connection = self._connect()
        try:
            source_counts = dict(connection.execute(
                "SELECT state, COUNT(*) FROM sources WHERE tenant_id = ? AND brand_id = ? GROUP BY state",
                (context.tenant_id, context.brand_id),
            ).fetchall())
            candidate_counts = dict(connection.execute(
                "SELECT state, COUNT(*) FROM fact_candidates WHERE tenant_id = ? AND brand_id = ? GROUP BY state",
                (context.tenant_id, context.brand_id),
            ).fetchall())
            approval_counts = dict(connection.execute(
                "SELECT state, COUNT(*) FROM approval_snapshots WHERE tenant_id = ? AND brand_id = ? GROUP BY state",
                (context.tenant_id, context.brand_id),
            ).fetchall())
            claims = [json.loads(row[0]) for row in connection.execute(
                "SELECT record_json FROM brand_twin_versions WHERE tenant_id = ? AND brand_id = ? ORDER BY version",
                (context.tenant_id, context.brand_id),
            ).fetchall()]
        finally:
            connection.close()
        modules: dict[str, bool] = {}
        lifecycle_state = "unknown"
        if authority_projection is not None:
            modules = authority_projection["modules"]
            lifecycle_state = authority_projection["lifecycle_state"]
        brand_profile: dict[str, Any] | None = None
        observatory: dict[str, Any] | None = None
        if self.brand_intelligence_path is not None:
            try:
                intelligence = BrandIntelligenceAuthority(self.brand_intelligence_path)
                reader = Principal(
                    context.workos_subject, "platform-assurance-reviewer", context.brand_id,
                )
                brand_profile = intelligence.operating_profile(reader)
                observatory = intelligence.observatory_summary(reader)
            except (BrandIntelligenceError, KeyError):
                brand_profile = None
                observatory = None
        return {
            "tenant_id": context.tenant_id, "brand_id": context.brand_id,
            "lifecycle_state": lifecycle_state, "modules": modules,
            "source_counts": source_counts, "candidate_counts": candidate_counts,
            "approval_counts": approval_counts, "brand_twin_claims": claims,
            "brand_profile": brand_profile, "observatory": observatory,
            "content": self.list_content(context),
            "sources": self.list_sources(context),
            "candidates": self.list_candidates(context),
            "approvals": self.list_approvals(context),
            "memberships": self.list_memberships(context),
            "invitations": self.list_invitations(context),
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

    def enforce_retention(self, *, now: datetime | None = None) -> dict[str, int]:
        """Apply the approved content-free telemetry and evidence retention rules."""

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        audit_cutoff = (current - timedelta(days=400)).isoformat().replace("+00:00", "Z")
        telemetry_cutoff = (current - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        reservation_cutoff = (current - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        recorded_at = current.isoformat().replace("+00:00", "Z")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            abandoned_reservations = connection.execute(
                "UPDATE upload_reservations SET state = 'expired', updated_at = ? "
                "WHERE state = 'reserved' AND created_at < ?",
                (recorded_at, reservation_cutoff),
            ).rowcount
            expired_reservations = connection.execute(
                "DELETE FROM upload_reservations WHERE state IN ('expired', 'cancelled') "
                "AND updated_at < ?",
                (telemetry_cutoff,),
            ).rowcount
            expired_sessions = connection.execute(
                "DELETE FROM sessions WHERE state != 'active' AND last_seen_at < ?",
                (telemetry_cutoff,),
            ).rowcount
            old_audit = connection.execute(
                "DELETE FROM portal_audit WHERE recorded_at < ?",
                (audit_cutoff,),
            ).rowcount
            connection.commit()
        finally:
            connection.close()
        return {
            "abandoned_upload_reservations": abandoned_reservations,
            "upload_reservations": expired_reservations,
            "sessions": expired_sessions, "audit_events": old_audit,
        }

    def _enforce_authoritative_access(
        self, context: PortalRequestContext,
    ) -> dict[str, Any] | None:
        if self.tenant_authority_path is None:
            return None
        try:
            authority = FleetTenantAuthority(self.tenant_authority_path)
            projection = authority.portal_access_projection(
                Principal(
                    actor_id=context.workos_subject,
                    role_id="platform-assurance-reviewer",
                    brand_id=context.brand_id,
                ),
                context.hostname,
                workos_organization_id=context.workos_organization_id,
            )
        except (FleetTenancyAuthorizationError, FleetTenancyError, KeyError) as exc:
            raise FleetPortalAuthorizationError(
                "current tenant authority denied portal access"
            ) from exc
        if (
            projection["tenant_id"] != context.tenant_id
            or projection["entitlement_version"] != context.entitlement_version
        ):
            raise FleetPortalAuthorizationError(
                "portal membership is stale against current tenant authority"
            )
        return projection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
            row = connection.execute("SELECT version FROM portal_schema WHERE id = 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO portal_schema VALUES (1, ?)", (_SCHEMA_VERSION,))
            elif int(row[0]) in {1, 2}:
                connection.execute(
                    "UPDATE portal_schema SET version = ? WHERE id = 1",
                    (_SCHEMA_VERSION,),
                )
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
CREATE TABLE IF NOT EXISTS invitations (
    invitation_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL, workos_organization_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    client_role TEXT NOT NULL, scopes_json TEXT NOT NULL, hostname TEXT NOT NULL,
    state TEXT NOT NULL, expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_reservations (
    reservation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, purpose TEXT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS upload_reservations_tenant_state
    ON upload_reservations (tenant_id, brand_id, state, created_at);
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
CREATE TABLE IF NOT EXISTS candidate_reviews (
    candidate_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    source_id TEXT NOT NULL, candidate_checksum TEXT NOT NULL,
    statement TEXT NOT NULL, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL,
    review_checksum TEXT NOT NULL, state TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES fact_candidates(candidate_id),
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
CREATE TABLE IF NOT EXISTS approval_candidates (
    approval_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY (approval_id) REFERENCES approval_snapshots(approval_id),
    FOREIGN KEY (candidate_id) REFERENCES fact_candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS brand_twin_versions (
    version_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brand_id TEXT NOT NULL,
    version INTEGER NOT NULL, candidate_id TEXT NOT NULL, source_id TEXT NOT NULL,
    approval_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL,
    checksum TEXT NOT NULL, source_checksum TEXT NOT NULL,
    approval_snapshot_checksum TEXT NOT NULL,
    approval_decision_checksum TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (tenant_id, brand_id, version),
    FOREIGN KEY (candidate_id) REFERENCES fact_candidates(candidate_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (approval_id) REFERENCES approval_snapshots(approval_id)
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
