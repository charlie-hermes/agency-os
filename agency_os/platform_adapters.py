"""Typed platform adapters plus persistent tenant evidence and artifacts.

This module is a local Gate 5 reference boundary.  It deliberately performs no
network calls and does not claim compatibility with an installed Paperclip or
Buzz service.  Paperclip-shaped state is authoritative; Buzz-shaped context can
only append a decision summary and cannot mutate task state, budget, or
approval.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ._approval_authority import (
    _FictionalApprovalAuthority,
    _FictionalAuditRetentionAuthority,
    _FictionalRecoveryAuthority,
)
from .contracts import (
    ContractError,
    canonical_bytes,
    canonical_checksum,
    finalize_record,
    parse_time,
    utc_now,
    verify_record,
)
from .sqlite_storage import (
    SQLiteStorageError,
    SQLiteStorageIdentity,
    prepare_sqlite_storage,
    validate_sqlite_storage,
)
from .store import (
    RECORD_ID_FIELDS,
    ROLE_READS,
    ROLE_WRITES,
    AuthorizationError,
    Principal,
)


class PlatformAdapterError(RuntimeError):
    """A typed platform request could not be accepted or persisted."""


class EvidenceStoreError(RuntimeError):
    """The persistent tenant evidence authority failed closed."""


class ArtifactStoreError(RuntimeError):
    """The persistent tenant artifact and learning authority failed closed."""


class WorkQueueError(RuntimeError):
    """The protected fictional work queue failed closed."""


TASK_STATUSES = frozenset(
    {"planned", "ready", "in_progress", "blocked", "done", "cancelled"}
)
TASK_TRANSITIONS = {
    "planned": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"in_progress", "blocked", "cancelled"}),
    "in_progress": frozenset({"blocked", "done", "cancelled"}),
    "blocked": frozenset({"ready", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}
EVIDENCE_WRITERS = frozenset(
    {
        "agency-director",
        "search-content-strategist",
        "growth-intelligence-analyst",
        "editorial-integrity-qa",
        "platform-assurance-reviewer",
    }
)
WORK_KINDS = frozenset({"internal", "external_write"})
WORKER_QUEUE_ROLES = frozenset(
    {
        "technical-implementation-specialist",
        "platform-assurance-reviewer",
        "brand-brief-steward",
        "search-content-strategist",
        "content-producer",
        "search-answer-optimiser",
        "visual-creative-specialist",
        "editorial-integrity-qa",
        "social-amplifier",
        "publishing-operator",
        "growth-intelligence-analyst",
    }
)
WORK_QUEUE_STATES = frozenset(
    {
        "READY",
        "LEASED",
        "RETRY_WAIT",
        "RECONCILIATION_REQUIRED",
        "DEAD_LETTER",
        "COMPLETED",
    }
)
WORK_ERROR_CLASSES = frozenset(
    {
        "INTERNAL_TRANSIENT",
        "INTERNAL_PERMANENT",
        "EXTERNAL_TIMEOUT",
        "EXTERNAL_REJECTED",
        "LEASE_EXPIRED",
        "TASK_DRIFT",
        "TENANT_OFFBOARDED",
    }
)
WORK_RECONCILIATION_OUTCOMES = frozenset(
    {"CONFIRMED_COMPLETED", "CONFIRMED_NO_WRITE", "DEAD_LETTER"}
)


def make_paperclip_task(
    *,
    issue_id: str,
    brand_id: str,
    campaign_id: str,
    task_type: str,
    title: str,
    dependencies: Sequence[str],
    acceptance_criteria: Sequence[str],
    budget_limit_minor: int,
    created_by: str,
    approval_required: bool = False,
    currency: str = "GBP",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create the first immutable version of a typed Paperclip task."""

    if not issue_id or not campaign_id or not task_type or not title:
        raise ContractError("task identity, type, campaign, and title are required")
    if not brand_id.startswith("brand_"):
        raise ContractError("brand_id must use the brand_ prefix")
    if not acceptance_criteria or any(not item for item in acceptance_criteria):
        raise ContractError("task requires non-empty acceptance criteria")
    if budget_limit_minor < 0:
        raise ContractError("task budget cannot be negative")
    if len(set(dependencies)) != len(dependencies) or issue_id in dependencies:
        raise ContractError("task dependencies must be unique and cannot include itself")
    timestamp = created_at or utc_now()
    parse_time(timestamp)
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "paperclip_task",
            "paperclip_issue_id": issue_id,
            "brand_id": brand_id,
            "campaign_id": campaign_id,
            "task_type": task_type,
            "title": title,
            "version": 1,
            "previous_checksum": None,
            "status": "planned",
            "dependencies": sorted(dependencies),
            "acceptance_criteria": list(acceptance_criteria),
            "approval_required": approval_required,
            "budget": {
                "currency": currency,
                "limit_minor": budget_limit_minor,
                "spent_minor": 0,
            },
            "completion_evidence_refs": [],
            "created_by": created_by,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )


def make_work_queue_item(
    *,
    work_item_id: str,
    brand_id: str,
    paperclip_issue_id: str,
    paperclip_task_checksum: str,
    work_kind: str,
    worker_role: str,
    payload: Mapping[str, Any],
    max_attempts: int,
    created_by: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create immutable fictional delivery work bound to one Paperclip version."""

    if not all(
        isinstance(value, str) and value
        for value in (
            work_item_id,
            paperclip_issue_id,
            paperclip_task_checksum,
            worker_role,
            created_by,
        )
    ):
        raise ContractError("work queue identity and Paperclip binding are required")
    if not brand_id.startswith("brand_"):
        raise ContractError("work queue brand_id must use the brand_ prefix")
    if work_kind not in WORK_KINDS:
        raise ContractError("work queue kind is invalid")
    if worker_role not in WORKER_QUEUE_ROLES:
        raise ContractError("work queue worker role is invalid")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise ContractError("work queue max_attempts must be an integer")
    if not 1 <= max_attempts <= 5:
        raise ContractError("work queue max_attempts must be between 1 and 5")
    if not isinstance(payload, Mapping):
        raise ContractError("work queue payload must be an object")
    timestamp = created_at or utc_now()
    parse_time(timestamp)
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "work_queue_item",
            "work_item_id": work_item_id,
            "brand_id": brand_id,
            "paperclip_issue_id": paperclip_issue_id,
            "paperclip_task_checksum": paperclip_task_checksum,
            "work_kind": work_kind,
            "worker_role": worker_role,
            "payload": copy.deepcopy(dict(payload)),
            "max_attempts": max_attempts,
            "created_by": created_by,
            "created_at": timestamp,
        }
    )


def make_approver_policy(
    *,
    policy_id: str,
    brand_id: str,
    revision: int,
    permitted_approver_ids: Sequence[str],
    issued_by: str,
    effective_at: str,
    previous_policy_checksum: str | None = None,
) -> dict[str, Any]:
    """Create one immutable revision of a brand-owned approver catalogue."""

    if not policy_id or not brand_id.startswith("brand_") or not issued_by:
        raise ContractError("approver policy identity, brand, and issuer are required")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractError("approver policy revision must be a positive integer")
    if not permitted_approver_ids or len(set(permitted_approver_ids)) != len(
        permitted_approver_ids
    ):
        raise ContractError("approver policy requires unique permitted actors")
    if any(not actor_id for actor_id in permitted_approver_ids):
        raise ContractError("approver policy actor IDs cannot be empty")
    if revision == 1 and previous_policy_checksum is not None:
        raise ContractError("first approver policy revision cannot have a predecessor")
    if revision > 1 and not previous_policy_checksum:
        raise ContractError("later approver policy revision requires its predecessor")
    parse_time(effective_at)
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "approver_policy",
            "policy_id": policy_id,
            "brand_id": brand_id,
            "revision": revision,
            "previous_policy_checksum": previous_policy_checksum,
            "permitted_approver_ids": sorted(permitted_approver_ids),
            "issued_by": issued_by,
            "effective_at": effective_at,
        }
    )


def make_buzz_context_packet(
    *,
    context_id: str,
    brand_id: str,
    campaign_id: str,
    paperclip_issue_id: str,
    purpose: str,
    decision_needed: str,
    participants: Sequence[str],
    source_artifact_ids: Sequence[str],
    constraints: Sequence[str],
    deadline: str,
    exit_condition: str,
    created_by: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create the typed packet used to open bounded Buzz collaboration."""

    if not all(
        (
            context_id,
            campaign_id,
            paperclip_issue_id,
            purpose,
            decision_needed,
            exit_condition,
        )
    ):
        raise ContractError("Buzz context identity and decision fields are required")
    if not brand_id.startswith("brand_"):
        raise ContractError("brand_id must use the brand_ prefix")
    if not participants or len(set(participants)) != len(participants):
        raise ContractError("Buzz context requires unique participants")
    parse_time(deadline)
    timestamp = created_at or utc_now()
    parse_time(timestamp)
    if parse_time(deadline) <= parse_time(timestamp):
        raise ContractError("Buzz context deadline must be in the future")
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "buzz_context_packet",
            "context_id": context_id,
            "brand_id": brand_id,
            "campaign_id": campaign_id,
            "paperclip_issue_id": paperclip_issue_id,
            "purpose": purpose,
            "decision_needed": decision_needed,
            "participants": list(participants),
            "source_artifact_ids": list(source_artifact_ids),
            "constraints": list(constraints),
            "deadline": deadline,
            "exit_condition": exit_condition,
            "created_by": created_by,
            "created_at": timestamp,
        }
    )


def make_evidence_record(
    *,
    evidence_id: str,
    brand_id: str,
    paperclip_issue_id: str,
    source_ref: str,
    source_class: str,
    retrieved_at: str,
    claim: str,
    extract: str,
    confidence: float,
    created_by: str,
) -> dict[str, Any]:
    """Create immutable, cited evidence for one tenant and Paperclip task."""

    if not all((evidence_id, paperclip_issue_id, source_ref, claim, extract)):
        raise ContractError("evidence identity, source, claim, and extract are required")
    if not brand_id.startswith("brand_"):
        raise ContractError("brand_id must use the brand_ prefix")
    if source_class not in {"primary", "first_party", "internal_test"}:
        raise ContractError("evidence source_class is not allowed")
    if isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise ContractError("evidence confidence must be between zero and one")
    parse_time(retrieved_at)
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "evidence_record",
            "evidence_id": evidence_id,
            "brand_id": brand_id,
            "paperclip_issue_id": paperclip_issue_id,
            "source_ref": source_ref,
            "source_class": source_class,
            "retrieved_at": retrieved_at,
            "claim": claim,
            "extract": extract,
            "confidence": confidence,
            "created_by": created_by,
        }
    )


class _SQLitePlatformDatabase:
    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float,
        error_type: type[RuntimeError],
    ) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("platform authority requires a durable file path")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.error_type = error_type
        try:
            self.identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise error_type("unsafe platform authority storage") from exc
        self._initialize()

    def _initialize(self) -> None:
        connection = self.connect()
        try:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise self.error_type("platform authority requires SQLite WAL mode")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paperclip_task_versions (
                    brand_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, issue_id, version),
                    UNIQUE (brand_id, issue_id, checksum)
                );
                CREATE TABLE IF NOT EXISTS paperclip_approver_policies (
                    brand_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, policy_id, revision),
                    UNIQUE (brand_id, policy_id, checksum)
                );
                CREATE TABLE IF NOT EXISTS paperclip_approvals (
                    brand_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    task_checksum TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, approval_id)
                );
                CREATE TABLE IF NOT EXISTS paperclip_buzz_contexts (
                    brand_id TEXT NOT NULL,
                    context_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'archived')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, context_id)
                );
                CREATE TABLE IF NOT EXISTS paperclip_buzz_decisions (
                    brand_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    context_checksum TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, decision_id)
                );
                CREATE TABLE IF NOT EXISTS tenant_evidence (
                    brand_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, evidence_id)
                );
                CREATE TABLE IF NOT EXISTS tenant_artifacts (
                    brand_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, record_id)
                );
                CREATE INDEX IF NOT EXISTS tenant_artifacts_by_type
                    ON tenant_artifacts (brand_id, artifact_type, record_id);
                CREATE TABLE IF NOT EXISTS platform_work_queue (
                    brand_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    work_json TEXT NOT NULL,
                    work_checksum TEXT NOT NULL,
                    work_kind TEXT NOT NULL
                        CHECK (work_kind IN ('internal', 'external_write')),
                    worker_role TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'READY',
                            'LEASED',
                            'RETRY_WAIT',
                            'RECONCILIATION_REQUIRED',
                            'DEAD_LETTER',
                            'COMPLETED'
                        )
                    ),
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    next_attempt_at TEXT,
                    leased_at TEXT,
                    lease_owner TEXT,
                    lease_token_hash TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    error_classes_json TEXT NOT NULL,
                    disposition_json TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, work_item_id)
                );
                CREATE INDEX IF NOT EXISTS platform_work_queue_available
                    ON platform_work_queue (
                        brand_id, worker_role, state, next_attempt_at, created_at
                    );
                CREATE TABLE IF NOT EXISTS tenant_queue_cancellations (
                    brand_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL UNIQUE,
                    evidence_ref TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    cancelled_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS platform_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    brand_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_audit_retention_policies (
                    brand_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, revision),
                    UNIQUE (brand_id, checksum)
                );
                CREATE TABLE IF NOT EXISTS tenant_audit_expirations (
                    brand_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    manifest_checksum TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    expired_before TEXT NOT NULL,
                    expired_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, receipt_id),
                    UNIQUE (brand_id, manifest_checksum)
                );
                """
            )
            buzz_context_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(paperclip_buzz_contexts)"
                ).fetchall()
            }
            if "state" not in buzz_context_columns:
                connection.execute(
                    """
                    ALTER TABLE paperclip_buzz_contexts
                    ADD COLUMN state TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'archived'))
                    """
                )
            connection.commit()
        except self.error_type:
            raise
        except sqlite3.Error as exc:
            raise self.error_type("could not initialize platform authority") from exc
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            validate_sqlite_storage(self.database_path, self.identity)
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            validate_sqlite_storage(self.database_path, self.identity)
            connection.execute(
                f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}"
            )
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise self.error_type("could not open platform authority") from exc


_DELETION_LEDGER_TOKEN = object()


class _SQLiteArtifactDeletionLedger:
    """Protected authority-wide tombstones shared by every recovery host."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        authority_id: str,
        timeout_seconds: float,
        allow_create: bool,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _DELETION_LEDGER_TOKEN:
            raise ArtifactStoreError("artifact deletion ledger construction denied")
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("artifact deletion ledger requires a durable file path")
        if not authority_id:
            raise ValueError("artifact deletion ledger authority_id is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        existed = self.database_path.exists()
        if not existed and not allow_create:
            raise ArtifactStoreError(
                "artifact deletion ledger has not been provisioned"
            )
        self.authority_id = authority_id
        self.timeout_seconds = timeout_seconds
        try:
            self.identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise ArtifactStoreError("unsafe artifact deletion ledger storage") from exc
        if existed:
            self._validate_existing()
        else:
            self._initialize_new()

    def _initialize_new(self) -> None:
        connection = self.connect()
        try:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise ArtifactStoreError(
                    "artifact deletion ledger requires SQLite WAL mode"
                )
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE deletion_ledger_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    authority_id TEXT NOT NULL
                );
                CREATE TABLE tenant_artifact_deletions (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL UNIQUE,
                    export_checksum TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    receipt_json TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id)
                );
                CREATE TABLE tenant_authority_offboardings (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL UNIQUE,
                    authority_manifest_checksum TEXT NOT NULL,
                    artifact_deletion_receipt_id TEXT NOT NULL,
                    queue_cancellation_receipt_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    offboarded_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id)
                );
                CREATE TABLE tenant_audit_retention_anchors (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    policy_checksum TEXT NOT NULL,
                    minimum_retention_days INTEGER NOT NULL,
                    anchored_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id, revision),
                    UNIQUE (authority_id, brand_id, policy_checksum)
                );
                CREATE TABLE tenant_audit_retention_intents (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    policy_checksum TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO deletion_ledger_metadata (singleton, authority_id)
                VALUES (1, ?)
                """,
                (self.authority_id,),
            )
            connection.commit()
        except ArtifactStoreError:
            raise
        except sqlite3.Error as exc:
            raise ArtifactStoreError(
                "could not initialize artifact deletion ledger"
            ) from exc
        finally:
            connection.close()

    def _validate_existing(self) -> None:
        connection = self.connect()
        try:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
            metadata = connection.execute(
                """
                SELECT authority_id FROM deletion_ledger_metadata
                WHERE singleton = 1
                """
            ).fetchone()
            deletion_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(tenant_artifact_deletions)"
                ).fetchall()
            }
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise ArtifactStoreError(
                    "artifact deletion ledger requires SQLite WAL mode"
                )
            if metadata != (self.authority_id,):
                raise ArtifactStoreError(
                    "artifact deletion ledger authority identity is invalid"
                )
            if deletion_columns != {
                "authority_id",
                "brand_id",
                "receipt_id",
                "export_checksum",
                "record_count",
                "receipt_json",
                "deleted_at",
            }:
                raise ArtifactStoreError(
                    "artifact deletion ledger schema is invalid"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tenant_authority_offboardings (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL UNIQUE,
                    authority_manifest_checksum TEXT NOT NULL,
                    artifact_deletion_receipt_id TEXT NOT NULL,
                    queue_cancellation_receipt_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    offboarded_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id)
                )
                """
            )
            offboarding_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(tenant_authority_offboardings)"
                ).fetchall()
            }
            if offboarding_columns != {
                "authority_id",
                "brand_id",
                "receipt_id",
                "authority_manifest_checksum",
                "artifact_deletion_receipt_id",
                "queue_cancellation_receipt_id",
                "evidence_ref",
                "receipt_json",
                "offboarded_at",
            }:
                raise ArtifactStoreError(
                    "tenant authority offboarding ledger schema is invalid"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tenant_audit_retention_anchors (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    policy_checksum TEXT NOT NULL,
                    minimum_retention_days INTEGER NOT NULL,
                    anchored_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id, revision),
                    UNIQUE (authority_id, brand_id, policy_checksum)
                )
                """
            )
            anchor_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(tenant_audit_retention_anchors)"
                ).fetchall()
            }
            if anchor_columns != {
                "authority_id",
                "brand_id",
                "revision",
                "policy_checksum",
                "minimum_retention_days",
                "anchored_at",
            }:
                raise ArtifactStoreError(
                    "audit retention anchor ledger schema is invalid"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tenant_audit_retention_intents (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    policy_checksum TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id)
                )
                """
            )
            intent_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(tenant_audit_retention_intents)"
                ).fetchall()
            }
            if intent_columns != {
                "authority_id",
                "brand_id",
                "revision",
                "policy_checksum",
                "policy_json",
                "created_at",
            }:
                raise ArtifactStoreError(
                    "audit retention intent ledger schema is invalid"
                )
            connection.commit()
        except ArtifactStoreError:
            raise
        except sqlite3.Error as exc:
            raise ArtifactStoreError(
                "could not validate artifact deletion ledger"
            ) from exc
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            validate_sqlite_storage(self.database_path, self.identity)
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            validate_sqlite_storage(self.database_path, self.identity)
            connection.execute(
                f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}"
            )
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise ArtifactStoreError(
                "could not open artifact deletion ledger"
            ) from exc

    def deleted_receipt(
        self,
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> tuple[str, str] | None:
        return connection.execute(
            """
            SELECT receipt_id, receipt_json FROM tenant_artifact_deletions
            WHERE authority_id = ? AND brand_id = ?
            """,
            (self.authority_id, brand_id),
        ).fetchone()

    def insert_deletion(
        self,
        connection: sqlite3.Connection,
        *,
        brand_id: str,
        receipt: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO tenant_artifact_deletions (
                authority_id,
                brand_id,
                receipt_id,
                export_checksum,
                record_count,
                receipt_json,
                deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.authority_id,
                brand_id,
                receipt["deletion_receipt_id"],
                receipt["export_checksum"],
                receipt["record_count"],
                canonical_bytes(receipt).decode("utf-8"),
                receipt["deleted_at"],
            ),
        )

    def audit_retention_anchors(
        self,
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> list[tuple[int, str, int, str]]:
        rows = connection.execute(
            """
            SELECT revision, policy_checksum, minimum_retention_days, anchored_at
            FROM tenant_audit_retention_anchors
            WHERE authority_id = ? AND brand_id = ?
            ORDER BY revision
            """,
            (self.authority_id, brand_id),
        ).fetchall()
        validated: list[tuple[int, str, int, str]] = []
        previous_days = 0
        for expected_revision, row in enumerate(rows, start=1):
            revision, policy_checksum, retention_days, anchored_at = row
            if (
                revision != expected_revision
                or not isinstance(policy_checksum, str)
                or not policy_checksum.startswith("sha256:")
                or isinstance(retention_days, bool)
                or not isinstance(retention_days, int)
                or not 1 <= retention_days <= 3650
                or retention_days < previous_days
                or not isinstance(anchored_at, str)
            ):
                raise ArtifactStoreError("audit retention anchor is invalid")
            parse_time(anchored_at)
            validated.append(
                (revision, policy_checksum, retention_days, anchored_at)
            )
            previous_days = retention_days
        return validated

    def insert_audit_retention_anchor(
        self,
        connection: sqlite3.Connection,
        *,
        brand_id: str,
        policy: Mapping[str, Any],
    ) -> None:
        anchors = self.audit_retention_anchors(connection, brand_id)
        expected_revision = len(anchors) + 1
        if (
            policy["revision"] != expected_revision
            or (
                anchors
                and policy["minimum_retention_days"] < anchors[-1][2]
            )
        ):
            raise ArtifactStoreError("audit retention anchor is invalid")
        connection.execute(
            """
            INSERT INTO tenant_audit_retention_anchors (
                authority_id, brand_id, revision, policy_checksum,
                minimum_retention_days, anchored_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self.authority_id,
                brand_id,
                policy["revision"],
                policy["content_checksum"],
                policy["minimum_retention_days"],
                policy["effective_at"],
            ),
        )

    def audit_retention_intents(
        self,
        connection: sqlite3.Connection,
        brand_id: str | None = None,
    ) -> list[tuple[str, int, str, str, str]]:
        if brand_id is None:
            rows = connection.execute(
                """
                SELECT brand_id, revision, policy_checksum, policy_json, created_at
                FROM tenant_audit_retention_intents
                WHERE authority_id = ? ORDER BY brand_id
                """,
                (self.authority_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT brand_id, revision, policy_checksum, policy_json, created_at
                FROM tenant_audit_retention_intents
                WHERE authority_id = ? AND brand_id = ?
                """,
                (self.authority_id, brand_id),
            ).fetchall()
        validated: list[tuple[str, int, str, str, str]] = []
        for row in rows:
            intent_brand_id, revision, checksum, policy_json, created_at = row
            if (
                not isinstance(intent_brand_id, str)
                or not intent_brand_id
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 1
                or not isinstance(checksum, str)
                or not checksum.startswith("sha256:")
                or not isinstance(policy_json, str)
                or not isinstance(created_at, str)
            ):
                raise ArtifactStoreError("audit retention intent is invalid")
            parse_time(created_at)
            validated.append(
                (intent_brand_id, revision, checksum, policy_json, created_at)
            )
        return validated

    def insert_audit_retention_intent(
        self,
        connection: sqlite3.Connection,
        *,
        brand_id: str,
        policy: Mapping[str, Any],
    ) -> None:
        policy_json = canonical_bytes(policy).decode("utf-8")
        expected = (
            brand_id,
            policy["revision"],
            policy["content_checksum"],
            policy_json,
            policy["effective_at"],
        )
        existing = self.audit_retention_intents(connection, brand_id)
        if existing:
            if existing != [expected]:
                raise ArtifactStoreError("audit retention intent conflicts")
            return
        connection.execute(
            """
            INSERT INTO tenant_audit_retention_intents (
                authority_id, brand_id, revision, policy_checksum,
                policy_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self.authority_id,
                brand_id,
                policy["revision"],
                policy["content_checksum"],
                policy_json,
                policy["effective_at"],
            ),
        )

    def complete_audit_retention_intent(
        self,
        connection: sqlite3.Connection,
        *,
        brand_id: str,
        policy: Mapping[str, Any],
    ) -> None:
        policy_json = canonical_bytes(policy).decode("utf-8")
        expected = (
            brand_id,
            policy["revision"],
            policy["content_checksum"],
            policy_json,
            policy["effective_at"],
        )
        if self.audit_retention_intents(connection, brand_id) != [expected]:
            raise ArtifactStoreError("audit retention intent is invalid")
        self.insert_audit_retention_anchor(
            connection,
            brand_id=brand_id,
            policy=policy,
        )
        cursor = connection.execute(
            """
            DELETE FROM tenant_audit_retention_intents
            WHERE authority_id = ? AND brand_id = ?
              AND revision = ? AND policy_checksum = ? AND policy_json = ?
            """,
            (
                self.authority_id,
                brand_id,
                policy["revision"],
                policy["content_checksum"],
                policy_json,
            ),
        )
        if cursor.rowcount != 1:
            raise ArtifactStoreError("audit retention intent is invalid")

    def authority_offboarding_receipt(
        self,
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> tuple[str, str] | None:
        return connection.execute(
            """
            SELECT receipt_id, receipt_json FROM tenant_authority_offboardings
            WHERE authority_id = ? AND brand_id = ?
            """,
            (self.authority_id, brand_id),
        ).fetchone()

    def insert_authority_offboarding(
        self,
        connection: sqlite3.Connection,
        *,
        brand_id: str,
        receipt: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO tenant_authority_offboardings (
                authority_id,
                brand_id,
                receipt_id,
                authority_manifest_checksum,
                artifact_deletion_receipt_id,
                queue_cancellation_receipt_id,
                evidence_ref,
                receipt_json,
                offboarded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.authority_id,
                brand_id,
                receipt["tenant_offboarding_receipt_id"],
                receipt["authority_manifest_checksum"],
                receipt["artifact_deletion_receipt_id"],
                receipt["queue_cancellation_receipt_id"],
                receipt["evidence_ref"],
                canonical_bytes(receipt).decode("utf-8"),
                receipt["offboarded_at"],
            ),
        )


_AUTHORITY_ADAPTER_TOKEN = object()


class _AuthorityPaperclipAdapter:
    """Durable, typed local stand-in for Paperclip's authoritative state."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
        approval_authority: _FictionalApprovalAuthority,
        audit_retention_authority: _FictionalAuditRetentionAuthority,
        deletion_ledger: _SQLiteArtifactDeletionLedger,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_ADAPTER_TOKEN:
            raise PlatformAdapterError(
                "Paperclip authority construction is denied"
            )
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=PlatformAdapterError,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._approval_authority = approval_authority
        self._audit_retention_authority = audit_retention_authority
        self._deletion_ledger = deletion_ledger
        self._audit_retention_failure_point: str | None = None

    def _inject_audit_retention_failure(self, point: str) -> None:
        if point not in {
            "after_intent_commit",
            "after_policy_commit",
            "after_anchor_commit",
        }:
            raise ValueError("audit retention failure point is invalid")
        self._audit_retention_failure_point = point

    def _maybe_fail_audit_retention(self, point: str) -> None:
        if self._audit_retention_failure_point == point:
            self._audit_retention_failure_point = None
            raise PlatformAdapterError(
                f"injected audit retention failure after {point.removeprefix('after_')}"
            )

    def create_task(self, principal: Principal, task: Mapping[str, Any]) -> str:
        self._require_director(principal)
        _validate_task(task)
        if (
            task["version"] != 1
            or task["previous_checksum"] is not None
            or task["status"] != "planned"
            or task["budget"]["spent_minor"] != 0
            or task["completion_evidence_refs"]
        ):
            raise ContractError("new Paperclip task has invalid initial state")
        if task["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant Paperclip task creation denied")
        if task["created_by"] != principal.actor_id:
            raise AuthorizationError("task creator does not match authenticated actor")
        record = copy.deepcopy(dict(task))
        issue_id = record["paperclip_issue_id"]
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_task_versions
                WHERE brand_id = ? AND issue_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (principal.brand_id, issue_id),
            ).fetchone()
            if row is not None:
                if json.loads(row[0]) != record:
                    raise ContractError(f"Paperclip task {issue_id!r} already exists")
                connection.commit()
                return issue_id
            self._insert_task_version(connection, record)
            self._insert_audit(
                connection, principal, "paperclip.task.created", issue_id
            )
            connection.commit()
            return issue_id
        except (AuthorizationError, ContractError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not create Paperclip task") from exc
        finally:
            connection.close()

    def get_task(self, principal: Principal, issue_id: str) -> dict[str, Any]:
        connection = self._database.connect()
        try:
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_task_versions
                WHERE brand_id = ? AND issue_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (principal.brand_id, issue_id),
            ).fetchone()
            if row is None:
                raise KeyError(issue_id)
            record = json.loads(row[0])
            _validate_task(record)
            return record
        except (KeyError, ContractError, PlatformAdapterError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError("could not read Paperclip task") from exc
        finally:
            connection.close()

    def set_status(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        new_status: str,
    ) -> dict[str, Any]:
        self._require_director(principal)
        if new_status not in TASK_STATUSES or new_status == "done":
            raise ContractError("use close_task for done; requested status is invalid")

        def mutate(connection: sqlite3.Connection, current: dict[str, Any]) -> None:
            if new_status not in TASK_TRANSITIONS[current["status"]]:
                raise ContractError(
                    f"invalid Paperclip transition {current['status']!r} -> {new_status!r}"
                )
            if new_status in {"ready", "in_progress"}:
                self._require_dependencies_done(connection, current)
            current["status"] = new_status

        return self._mutate_task(
            principal, issue_id, expected_checksum, "paperclip.task.status", mutate
        )

    def record_spend(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        amount_minor: int,
    ) -> dict[str, Any]:
        self._require_director(principal)
        if isinstance(amount_minor, bool) or amount_minor <= 0:
            raise ContractError("Paperclip spend must be a positive integer")

        def mutate(_connection: sqlite3.Connection, current: dict[str, Any]) -> None:
            budget = current["budget"]
            new_spend = budget["spent_minor"] + amount_minor
            if new_spend > budget["limit_minor"]:
                raise ContractError("Paperclip task budget would be exceeded")
            budget["spent_minor"] = new_spend

        return self._mutate_task(
            principal, issue_id, expected_checksum, "paperclip.task.spend", mutate
        )

    def register_approver_policy(
        self, principal: Principal, policy: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Append one authority-owned approver policy revision."""

        self._require_director(principal)
        _validate_approver_policy(policy)
        if policy["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant approver policy denied")
        if policy["issued_by"] != principal.actor_id:
            raise AuthorizationError("approver policy issuer does not match actor")
        record = copy.deepcopy(dict(policy))
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT revision, record_json FROM paperclip_approver_policies
                WHERE brand_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (principal.brand_id,),
            ).fetchone()
            if row is None:
                if (
                    record["revision"] != 1
                    or record["previous_policy_checksum"] is not None
                ):
                    raise ContractError("first approver policy revision is invalid")
            else:
                latest_revision = int(row[0])
                latest = json.loads(row[1])
                if record["revision"] == latest_revision:
                    if latest != record:
                        raise ContractError("approver policy revision is immutable")
                    connection.commit()
                    return record
                if (
                    record["policy_id"] != latest["policy_id"]
                    or record["revision"] != latest_revision + 1
                    or record["previous_policy_checksum"]
                    != latest["content_checksum"]
                ):
                    raise ContractError("approver policy revision chain is invalid")
            connection.execute(
                """
                INSERT INTO paperclip_approver_policies (
                    brand_id, policy_id, revision, record_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    record["policy_id"],
                    record["revision"],
                    canonical_bytes(record).decode("utf-8"),
                    record["content_checksum"],
                    record["effective_at"],
                ),
            )
            self._insert_audit(
                connection,
                principal,
                "paperclip.approver_policy.recorded",
                f"{record['policy_id']}:{record['revision']}",
            )
            connection.commit()
            return record
        except (AuthorizationError, ContractError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not register approver policy") from exc
        finally:
            connection.close()

    def record_approval(
        self,
        principal: Principal,
        *,
        approval_id: str,
        issue_id: str,
        expected_task_checksum: str,
        policy_id: str,
        policy_revision: int,
        policy_checksum: str,
        decision: str,
        decided_at: str,
        expires_at: str,
    ) -> dict[str, Any]:
        if principal.role_id != "human-approver":
            raise AuthorizationError("only a human approver may record approval")
        if not approval_id:
            raise ContractError("Paperclip approval_id is required")
        if decision not in {"APPROVED", "REJECTED"}:
            raise ContractError("Paperclip approval decision is invalid")
        decision_time = parse_time(decided_at)
        expiry_time = parse_time(expires_at)
        if decision_time >= expiry_time:
            raise ContractError("Paperclip approval expiry must follow its decision")
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            authority_time = _authority_now(self._clock)
            if decision_time > authority_time or expiry_time <= authority_time:
                raise ContractError(
                    "Paperclip approval is outside its authority time window"
                )
            current = self._read_current_task(
                connection, principal.brand_id, issue_id
            )
            if current["content_checksum"] != expected_task_checksum:
                raise ContractError("Paperclip approval task checksum drift")
            policy = self._read_active_approver_policy(
                connection, principal.brand_id, policy_id
            )
            if (
                policy["revision"] != policy_revision
                or policy["content_checksum"] != policy_checksum
                or parse_time(policy["effective_at"]) > decision_time
            ):
                raise ContractError("Paperclip approver policy is stale or ineffective")
            if principal.actor_id not in policy["permitted_approver_ids"]:
                raise AuthorizationError("actor is not permitted by approver policy")
            approval = self._approval_authority.attest(
                {
                    "schema_version": "1.0",
                    "artifact_type": "paperclip_task_approval",
                    "approval_id": approval_id,
                    "brand_id": principal.brand_id,
                    "paperclip_issue_id": issue_id,
                    "task_checksum": expected_task_checksum,
                    "policy_id": policy_id,
                    "policy_revision": policy_revision,
                    "policy_checksum": policy_checksum,
                    "decision": decision,
                    "approver_id": principal.actor_id,
                    "authority_role": principal.role_id,
                    "decided_at": decided_at,
                    "expires_at": expires_at,
                }
            )
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_approvals
                WHERE brand_id = ? AND approval_id = ?
                """,
                (principal.brand_id, approval_id),
            ).fetchone()
            if row is not None:
                if json.loads(row[0]) != approval:
                    raise ContractError(
                        f"Paperclip approval {approval_id!r} is immutable"
                    )
                connection.commit()
                return approval
            connection.execute(
                """
                INSERT INTO paperclip_approvals (
                    brand_id, approval_id, issue_id, task_checksum,
                    record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    approval_id,
                    issue_id,
                    expected_task_checksum,
                    canonical_bytes(approval).decode("utf-8"),
                    decided_at,
                ),
            )
            self._insert_audit(
                connection, principal, "paperclip.approval.recorded", approval_id
            )
            connection.commit()
            return approval
        except (AuthorizationError, ContractError, KeyError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not record Paperclip approval") from exc
        finally:
            connection.close()

    def close_task(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        *,
        evidence_refs: Sequence[str],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_director(principal)
        if not evidence_refs or len(set(evidence_refs)) != len(evidence_refs):
            raise ContractError("Paperclip closure requires unique evidence references")

        def mutate(connection: sqlite3.Connection, current: dict[str, Any]) -> None:
            if "done" not in TASK_TRANSITIONS[current["status"]]:
                raise ContractError("Paperclip task is not in a closable state")
            self._require_dependencies_done(connection, current)
            if current["approval_required"]:
                self._require_valid_approval(
                    connection, current, approval_id, expected_checksum
                )
            self._require_evidence(connection, current, evidence_refs)
            current["status"] = "done"
            current["completion_evidence_refs"] = list(evidence_refs)

        return self._mutate_task(
            principal, issue_id, expected_checksum, "paperclip.task.closed", mutate
        )

    def record_buzz_context(
        self, principal: Principal, packet: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Retain an immutable Buzz context link without changing task state."""

        self._require_director(principal)
        _validate_buzz_context(packet)
        if packet["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant Buzz context write-back denied")
        if packet["created_by"] != principal.actor_id:
            raise AuthorizationError("Buzz context author does not match actor")
        record = copy.deepcopy(dict(packet))
        context_id = record["context_id"]
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            authority_time = _authority_now(self._clock)
            if (
                parse_time(record["created_at"]) > authority_time
                or parse_time(record["deadline"]) <= authority_time
            ):
                raise ContractError("Buzz context is outside its authority time window")
            current = self._read_current_task(
                connection, principal.brand_id, record["paperclip_issue_id"]
            )
            if current["campaign_id"] != record["campaign_id"]:
                raise ContractError("Buzz context campaign does not match Paperclip")
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_buzz_contexts
                WHERE brand_id = ? AND context_id = ?
                """,
                (principal.brand_id, context_id),
            ).fetchone()
            if row is not None:
                if json.loads(row[0]) != record:
                    raise ContractError(f"Buzz context {context_id!r} is immutable")
                connection.commit()
                return record
            connection.execute(
                """
                INSERT INTO paperclip_buzz_contexts (
                    brand_id, context_id, issue_id, record_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    context_id,
                    record["paperclip_issue_id"],
                    canonical_bytes(record).decode("utf-8"),
                    record["content_checksum"],
                    record["created_at"],
                ),
            )
            self._insert_audit(
                connection, principal, "paperclip.buzz_context.recorded", context_id
            )
            connection.commit()
            return record
        except (AuthorizationError, ContractError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not persist Buzz context") from exc
        finally:
            connection.close()

    def get_buzz_context(
        self, principal: Principal, context_id: str
    ) -> dict[str, Any]:
        connection = self._database.connect()
        try:
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_buzz_contexts
                WHERE brand_id = ? AND context_id = ?
                """,
                (principal.brand_id, context_id),
            ).fetchone()
            if row is None:
                raise KeyError(context_id)
            record = json.loads(row[0])
            _validate_buzz_context(record)
            return record
        except (KeyError, ContractError, PlatformAdapterError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError("could not read Buzz context") from exc
        finally:
            connection.close()

    def get_buzz_context_state(self, principal: Principal, context_id: str) -> str:
        connection = self._database.connect()
        try:
            row = connection.execute(
                """
                SELECT state FROM paperclip_buzz_contexts
                WHERE brand_id = ? AND context_id = ?
                """,
                (principal.brand_id, context_id),
            ).fetchone()
            if row is None:
                raise KeyError(context_id)
            state = str(row[0])
            if state not in {"open", "archived"}:
                raise ContractError("stored Buzz context state is invalid")
            return state
        except (KeyError, ContractError, PlatformAdapterError):
            raise
        except sqlite3.Error as exc:
            raise PlatformAdapterError("could not read Buzz context state") from exc
        finally:
            connection.close()

    def archive_buzz_context(self, principal: Principal, context_id: str) -> None:
        self._require_director(principal)
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state FROM paperclip_buzz_contexts
                WHERE brand_id = ? AND context_id = ?
                """,
                (principal.brand_id, context_id),
            ).fetchone()
            if row is None:
                raise KeyError(context_id)
            if row[0] == "archived":
                connection.commit()
                return
            if row[0] != "open":
                raise ContractError("stored Buzz context state is invalid")
            connection.execute(
                """
                UPDATE paperclip_buzz_contexts SET state = 'archived'
                WHERE brand_id = ? AND context_id = ?
                """,
                (principal.brand_id, context_id),
            )
            self._insert_audit(
                connection, principal, "paperclip.buzz_context.archived", context_id
            )
            connection.commit()
        except (AuthorizationError, ContractError, KeyError, PlatformAdapterError):
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not archive Buzz context") from exc
        finally:
            connection.close()

    def record_buzz_decision(
        self, principal: Principal, decision: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Append a Buzz decision summary without changing task authority."""

        self._require_director(principal)
        _validate_buzz_decision(decision)
        if decision["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant Buzz decision write-back denied")
        if decision["recorded_by"] != principal.actor_id:
            raise AuthorizationError("Buzz decision author does not match actor")
        record = copy.deepcopy(dict(decision))
        decision_id = record["decision_id"]
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            context_row = connection.execute(
                """
                SELECT record_json, state FROM paperclip_buzz_contexts
                WHERE brand_id = ? AND context_id = ?
                """,
                (principal.brand_id, record["context_id"]),
            ).fetchone()
            if context_row is None:
                raise KeyError(record["context_id"])
            context = json.loads(context_row[0])
            _validate_buzz_context(context)
            if context_row[1] != "open":
                raise ContractError("Buzz context is already archived")
            if (
                context["content_checksum"] != record["context_checksum"]
                or context["paperclip_issue_id"] != record["paperclip_issue_id"]
                or context["campaign_id"] != record["campaign_id"]
            ):
                raise ContractError("Buzz decision does not match retained context")
            authority_time = _authority_now(self._clock)
            recorded_at = parse_time(record["recorded_at"])
            deadline = parse_time(context["deadline"])
            if (
                authority_time >= deadline
                or recorded_at >= deadline
                or recorded_at < parse_time(context["created_at"])
                or recorded_at > authority_time
            ):
                raise ContractError("Buzz decision is outside its authority time window")
            current = self._read_current_task(
                connection, principal.brand_id, record["paperclip_issue_id"]
            )
            if current["campaign_id"] != record["campaign_id"]:
                raise ContractError("Buzz decision campaign does not match Paperclip")
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_buzz_decisions
                WHERE brand_id = ? AND decision_id = ?
                """,
                (principal.brand_id, decision_id),
            ).fetchone()
            if row is not None:
                if json.loads(row[0]) != record:
                    raise ContractError(f"Buzz decision {decision_id!r} is immutable")
                connection.commit()
                return record
            connection.execute(
                """
                INSERT INTO paperclip_buzz_decisions (
                    brand_id, decision_id, issue_id, context_checksum,
                    record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    decision_id,
                    record["paperclip_issue_id"],
                    record["context_checksum"],
                    canonical_bytes(record).decode("utf-8"),
                    record["recorded_at"],
                ),
            )
            self._insert_audit(
                connection, principal, "paperclip.buzz_decision.recorded", decision_id
            )
            connection.commit()
            return record
        except (AuthorizationError, ContractError, KeyError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not persist Buzz decision") from exc
        finally:
            connection.close()

    def get_buzz_decision(
        self, principal: Principal, decision_id: str
    ) -> dict[str, Any]:
        connection = self._database.connect()
        try:
            row = connection.execute(
                """
                SELECT record_json FROM paperclip_buzz_decisions
                WHERE brand_id = ? AND decision_id = ?
                """,
                (principal.brand_id, decision_id),
            ).fetchone()
            if row is None:
                raise KeyError(decision_id)
            record = json.loads(row[0])
            _validate_buzz_decision(record)
            return record
        except (KeyError, ContractError, PlatformAdapterError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError("could not read Buzz decision") from exc
        finally:
            connection.close()

    def audit_events(self, principal: Principal) -> list[dict[str, Any]]:
        if principal.role_id not in {
            "agency-director",
            "platform-assurance-reviewer",
        }:
            raise AuthorizationError("role cannot inspect platform audit")
        connection = self._database.connect()
        try:
            rows = connection.execute(
                """
                SELECT event_json FROM platform_audit
                WHERE brand_id = ? ORDER BY sequence
                """,
                (principal.brand_id,),
            ).fetchall()
            records = [json.loads(row[0]) for row in rows]
            for record in records:
                verify_record(record)
            return records
        except (AuthorizationError, ContractError, PlatformAdapterError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError("could not read platform audit") from exc
        finally:
            connection.close()

    def set_audit_retention_policy(
        self,
        principal: Principal,
        *,
        minimum_retention_days: int,
        evidence_ref: str,
    ) -> dict[str, Any]:
        self._require_director(principal)
        if (
            isinstance(minimum_retention_days, bool)
            or not isinstance(minimum_retention_days, int)
            or not 1 <= minimum_retention_days <= 3650
        ):
            raise ContractError(
                "audit retention must be an explicit value from 1 to 3650 days"
            )
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.startswith("evidence://")
            or len(evidence_ref) > 512
        ):
            raise ContractError("audit retention policy evidence is required")
        pending = self._pending_audit_retention_policy(principal.brand_id)
        if pending is not None:
            if (
                pending["minimum_retention_days"] != minimum_retention_days
                or pending["evidence_ref"] != evidence_ref
                or pending["approved_by"] != principal.actor_id
            ):
                raise ContractError(
                    "conflicting audit retention policy recovery denied"
                )
            self.reconcile_audit_retention_intents(principal.brand_id)
            return pending

        connection: sqlite3.Connection | None = None
        intent_connection: sqlite3.Connection | None = None
        policy: dict[str, Any] | None = None
        try:
            intent_connection = self._deletion_ledger.connect()
            intent_connection.execute("BEGIN IMMEDIATE")
            if self._deletion_ledger.audit_retention_intents(
                intent_connection, principal.brand_id
            ):
                raise PlatformAdapterError("audit retention recovery is pending")
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT 1 FROM tenant_audit_retention_policies
                WHERE brand_id = ? LIMIT 1
                """,
                (principal.brand_id,),
            ).fetchone()
            current = None
            if row is not None:
                current = self._current_audit_retention_policy(
                    connection,
                    principal.brand_id,
                    anchor_connection=intent_connection,
                )
                if minimum_retention_days < current["minimum_retention_days"]:
                    raise ContractError("audit retention cannot be shortened")
                if (
                    minimum_retention_days == current["minimum_retention_days"]
                    and evidence_ref == current["evidence_ref"]
                ):
                    intent_connection.commit()
                    connection.commit()
                    return current
            elif self._deletion_ledger.audit_retention_anchors(
                intent_connection, principal.brand_id
            ):
                raise PlatformAdapterError("audit retention policy is invalid")
            effective_at = _authority_now(self._clock).isoformat()
            policy = self._audit_retention_authority.attest(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_audit_retention_policy",
                    "brand_id": principal.brand_id,
                    "revision": 1 if current is None else current["revision"] + 1,
                    "previous_checksum": (
                        None if current is None else current["content_checksum"]
                    ),
                    "minimum_retention_days": minimum_retention_days,
                    "evidence_ref": evidence_ref,
                    "approved_by": principal.actor_id,
                    "effective_at": effective_at,
                }
            )
            self._deletion_ledger.insert_audit_retention_intent(
                intent_connection,
                brand_id=principal.brand_id,
                policy=policy,
            )
            intent_connection.commit()
            connection.commit()
            self._maybe_fail_audit_retention("after_intent_commit")
        except (AuthorizationError, ContractError, PlatformAdapterError):
            if connection is not None:
                _rollback(connection)
            if intent_connection is not None:
                _rollback(intent_connection)
            raise
        except (
            ArtifactStoreError,
            json.JSONDecodeError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as exc:
            if connection is not None:
                _rollback(connection)
            if intent_connection is not None:
                _rollback(intent_connection)
            raise PlatformAdapterError(
                "could not record audit retention policy"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
            if intent_connection is not None:
                intent_connection.close()
        if policy is None:  # pragma: no cover - guarded by successful intent creation
            raise PlatformAdapterError("audit retention policy was not recorded")
        self.reconcile_audit_retention_intents(principal.brand_id)
        return policy

    def reconcile_audit_retention_intents(
        self, brand_id: str | None = None
    ) -> None:
        intent_connection = self._deletion_ledger.connect()
        try:
            brands = [
                row[0]
                for row in self._deletion_ledger.audit_retention_intents(
                    intent_connection, brand_id
                )
            ]
        except ArtifactStoreError as exc:
            raise PlatformAdapterError(
                "audit retention recovery is invalid"
            ) from exc
        finally:
            intent_connection.close()
        for pending_brand_id in brands:
            self._reconcile_audit_retention_intent(pending_brand_id)

    def _pending_audit_retention_policy(
        self, brand_id: str
    ) -> dict[str, Any] | None:
        connection = self._deletion_ledger.connect()
        try:
            intents = self._deletion_ledger.audit_retention_intents(
                connection, brand_id
            )
            if not intents:
                return None
            intent_brand_id, revision, checksum, policy_json, created_at = intents[0]
            policy = self._validated_audit_retention_policy(
                json.loads(policy_json),
                brand_id,
                self._audit_retention_authority,
            )
            if (
                intent_brand_id != brand_id
                or revision != policy["revision"]
                or checksum != policy["content_checksum"]
                or created_at != policy["effective_at"]
            ):
                raise PlatformAdapterError("audit retention recovery is invalid")
            return policy
        except (ContractError, PlatformAdapterError):
            raise
        except (ArtifactStoreError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise PlatformAdapterError("audit retention recovery is invalid") from exc
        finally:
            connection.close()

    def _reconcile_audit_retention_intent(self, brand_id: str) -> None:
        connection: sqlite3.Connection | None = None
        intent_connection: sqlite3.Connection | None = None
        try:
            intent_connection = self._deletion_ledger.connect()
            intent_connection.execute("BEGIN IMMEDIATE")
            intents = self._deletion_ledger.audit_retention_intents(
                intent_connection, brand_id
            )
            if not intents:
                intent_connection.commit()
                return
            intent_brand_id, revision, checksum, policy_json, created_at = intents[0]
            policy = self._validated_audit_retention_policy(
                json.loads(policy_json),
                brand_id,
                self._audit_retention_authority,
            )
            if (
                intent_brand_id != brand_id
                or revision != policy["revision"]
                or checksum != policy["content_checksum"]
                or created_at != policy["effective_at"]
            ):
                raise PlatformAdapterError("audit retention recovery is invalid")
            anchors = self._deletion_ledger.audit_retention_anchors(
                intent_connection, brand_id
            )
            if (
                policy["revision"] != len(anchors) + 1
                or policy["previous_checksum"]
                != (None if not anchors else anchors[-1][1])
                or (
                    anchors
                    and policy["minimum_retention_days"] < anchors[-1][2]
                )
            ):
                raise PlatformAdapterError("audit retention recovery is invalid")

            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            policies = self._audit_retention_policy_rows(connection, brand_id)
            if len(policies) not in {len(anchors), len(anchors) + 1}:
                raise PlatformAdapterError("audit retention recovery is invalid")
            for stored_policy, anchor in zip(
                policies[: len(anchors)], anchors, strict=True
            ):
                if anchor != (
                    stored_policy["revision"],
                    stored_policy["content_checksum"],
                    stored_policy["minimum_retention_days"],
                    stored_policy["effective_at"],
                ):
                    raise PlatformAdapterError("audit retention recovery is invalid")
            if len(policies) == len(anchors):
                connection.execute(
                    """
                    INSERT INTO tenant_audit_retention_policies (
                        brand_id, revision, record_json, checksum, effective_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        brand_id,
                        policy["revision"],
                        canonical_bytes(policy).decode("utf-8"),
                        policy["content_checksum"],
                        policy["effective_at"],
                    ),
                )
                self._insert_audit(
                    connection,
                    Principal(
                        policy["approved_by"], "agency-director", brand_id
                    ),
                    "authority.audit_retention_policy.recorded",
                    policy["content_checksum"],
                )
            elif policies[-1] != policy:
                raise PlatformAdapterError("audit retention recovery is invalid")
            connection.commit()
            self._maybe_fail_audit_retention("after_policy_commit")
            self._deletion_ledger.complete_audit_retention_intent(
                intent_connection,
                brand_id=brand_id,
                policy=policy,
            )
            intent_connection.commit()
            self._maybe_fail_audit_retention("after_anchor_commit")
        except PlatformAdapterError:
            if connection is not None:
                _rollback(connection)
            if intent_connection is not None:
                _rollback(intent_connection)
            raise
        except (
            ArtifactStoreError,
            json.JSONDecodeError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as exc:
            if connection is not None:
                _rollback(connection)
            if intent_connection is not None:
                _rollback(intent_connection)
            raise PlatformAdapterError("audit retention recovery is invalid") from exc
        finally:
            if connection is not None:
                connection.close()
            if intent_connection is not None:
                intent_connection.close()

    def _audit_retention_policy_rows(
        self, connection: sqlite3.Connection, brand_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT revision, record_json, checksum, effective_at
            FROM tenant_audit_retention_policies
            WHERE brand_id = ? ORDER BY revision
            """,
            (brand_id,),
        ).fetchall()
        policies: list[dict[str, Any]] = []
        previous_checksum: str | None = None
        previous_days = 0
        for expected_revision, row in enumerate(rows, start=1):
            policy = self._validated_audit_retention_policy(
                json.loads(row[1]), brand_id, self._audit_retention_authority
            )
            if (
                row[0] != expected_revision
                or policy["revision"] != row[0]
                or policy["previous_checksum"] != previous_checksum
                or policy["content_checksum"] != row[2]
                or policy["effective_at"] != row[3]
                or policy["minimum_retention_days"] < previous_days
            ):
                raise PlatformAdapterError("audit retention policy is invalid")
            policies.append(policy)
            previous_checksum = policy["content_checksum"]
            previous_days = policy["minimum_retention_days"]
        return policies

    def audit_retention_policy(self, principal: Principal) -> dict[str, Any]:
        self._require_audit_reader(principal)
        connection = self._database.connect()
        try:
            return self._current_audit_retention_policy(
                connection, principal.brand_id
            )
        except (AuthorizationError, ContractError, PlatformAdapterError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError(
                "could not read audit retention policy"
            ) from exc
        finally:
            connection.close()

    def audit_telemetry(self, principal: Principal) -> dict[str, Any]:
        self._require_audit_reader(principal)
        connection = self._database.connect()
        try:
            policy = self._current_audit_retention_policy(
                connection, principal.brand_id
            )
            now = _authority_now(self._clock)
            eligible_before = now - timedelta(
                days=policy["minimum_retention_days"]
            )
            rows = self._audit_rows(connection, principal.brand_id)
            event_type_counts: dict[str, int] = {}
            eligible_event_count = 0
            created_times: list[datetime] = []
            for _sequence, event, created_at in rows:
                event_type = event["event_type"]
                event_type_counts[event_type] = event_type_counts.get(
                    event_type, 0
                ) + 1
                created_time = parse_time(created_at)
                created_times.append(created_time)
                if created_time < eligible_before:
                    eligible_event_count += 1
            return finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_audit_telemetry",
                    "brand_id": principal.brand_id,
                    "policy_revision": policy["revision"],
                    "policy_checksum": policy["content_checksum"],
                    "observed_at": now.isoformat(),
                    "event_count": len(rows),
                    "eligible_event_count": eligible_event_count,
                    "event_type_counts": dict(sorted(event_type_counts.items())),
                    "oldest_event_at": (
                        min(created_times).isoformat() if created_times else None
                    ),
                    "newest_event_at": (
                        max(created_times).isoformat() if created_times else None
                    ),
                }
            )
        except (AuthorizationError, ContractError, PlatformAdapterError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError("could not read audit telemetry") from exc
        finally:
            connection.close()

    def prepare_audit_expiration(self, principal: Principal) -> dict[str, Any]:
        self._require_director(principal)
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            policy = self._current_audit_retention_policy(
                connection, principal.brand_id
            )
            now = _authority_now(self._clock)
            expired_before = now - timedelta(
                days=policy["minimum_retention_days"]
            )
            state, _sequences = self._audit_expiration_state(
                connection,
                principal.brand_id,
                policy,
                expired_before,
            )
            manifest = finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_audit_expiration_manifest",
                    **state,
                    "audit_expiration_manifest_checksum": canonical_checksum(
                        state
                    ),
                    "prepared_at": now.isoformat(),
                }
            )
            connection.commit()
            return manifest
        except (AuthorizationError, ContractError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError(
                "could not prepare audit expiration"
            ) from exc
        finally:
            connection.close()

    def expire_audit_events(
        self,
        principal: Principal,
        *,
        manifest: Mapping[str, Any],
        evidence_ref: str,
    ) -> dict[str, Any]:
        self._require_director(principal)
        prepared = copy.deepcopy(dict(manifest))
        verify_record(prepared)
        if (
            set(prepared)
            != {
                "schema_version",
                "artifact_type",
                "brand_id",
                "policy_revision",
                "policy_checksum",
                "expired_before",
                "event_count",
                "events_checksum",
                "audit_expiration_manifest_checksum",
                "prepared_at",
                "content_checksum",
            }
            or prepared.get("schema_version") != "1.0"
            or prepared.get("artifact_type")
            != "tenant_audit_expiration_manifest"
            or prepared.get("brand_id") != principal.brand_id
            or not isinstance(prepared.get("policy_revision"), int)
            or isinstance(prepared.get("policy_revision"), bool)
            or not isinstance(prepared.get("policy_checksum"), str)
            or not prepared["policy_checksum"].startswith("sha256:")
            or not isinstance(prepared.get("expired_before"), str)
            or not isinstance(prepared.get("prepared_at"), str)
            or isinstance(prepared.get("event_count"), bool)
            or not isinstance(prepared.get("event_count"), int)
            or prepared["event_count"] < 0
            or not isinstance(prepared.get("events_checksum"), str)
            or not prepared["events_checksum"].startswith("sha256:")
            or not isinstance(
                prepared.get("audit_expiration_manifest_checksum"), str
            )
            or not prepared["audit_expiration_manifest_checksum"].startswith(
                "sha256:"
            )
        ):
            raise ContractError("audit expiration manifest is invalid")
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.startswith("evidence://")
            or len(evidence_ref) > 512
        ):
            raise ContractError("audit expiration evidence is required")
        evidence_reference = self._audit_retention_authority.evidence_reference(
            principal.brand_id,
            evidence_ref,
        )
        expired_before = parse_time(prepared["expired_before"])
        prepared_at = parse_time(prepared["prepared_at"])
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                """
                SELECT receipt_id, manifest_checksum, receipt_json,
                       expired_before, expired_at
                FROM tenant_audit_expirations
                WHERE brand_id = ? AND manifest_checksum = ?
                """,
                (
                    principal.brand_id,
                    prepared["audit_expiration_manifest_checksum"],
                ),
            ).fetchone()
            if existing_row is not None:
                existing = self._validated_audit_expiration_receipt(
                    json.loads(existing_row[2]), principal.brand_id
                )
                if (
                    existing["audit_expiration_receipt_id"] != existing_row[0]
                    or existing["audit_expiration_manifest_checksum"]
                    != existing_row[1]
                    or existing["expired_before"] != existing_row[3]
                    or existing["expired_at"] != existing_row[4]
                ):
                    raise PlatformAdapterError(
                        "audit expiration evidence is invalid"
                    )
                if existing["evidence_reference"] != evidence_reference:
                    raise ContractError("audit expiration receipt is immutable")
                connection.commit()
                return existing
            policy = self._current_audit_retention_policy(
                connection, principal.brand_id
            )
            now = _authority_now(self._clock)
            if (
                prepared["policy_revision"] != policy["revision"]
                or prepared["policy_checksum"] != policy["content_checksum"]
                or prepared_at > now
                or expired_before
                > now - timedelta(days=policy["minimum_retention_days"])
            ):
                raise ContractError("audit expiration policy is stale or invalid")
            state, sequences = self._audit_expiration_state(
                connection,
                principal.brand_id,
                policy,
                expired_before,
            )
            if (
                prepared["event_count"] != state["event_count"]
                or prepared["events_checksum"] != state["events_checksum"]
                or prepared["audit_expiration_manifest_checksum"]
                != canonical_checksum(state)
            ):
                raise ContractError("audit expiration manifest is stale or invalid")
            if not sequences:
                raise ContractError("no audit events are eligible for expiration")
            placeholders = ", ".join("?" for _sequence in sequences)
            connection.execute(
                f"DELETE FROM platform_audit WHERE brand_id = ? "
                f"AND sequence IN ({placeholders})",
                (principal.brand_id, *sequences),
            )
            expired_at = now.isoformat()
            receipt_seed = {
                "brand_id": principal.brand_id,
                "policy_revision": policy["revision"],
                "policy_checksum": policy["content_checksum"],
                "audit_expiration_manifest_checksum": prepared[
                    "audit_expiration_manifest_checksum"
                ],
                "expired_before": prepared["expired_before"],
                "event_count": len(sequences),
                "events_checksum": state["events_checksum"],
                "evidence_reference": evidence_reference,
                "expired_at": expired_at,
            }
            receipt = finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_audit_expiration_receipt",
                    "audit_expiration_receipt_id": canonical_checksum(
                        receipt_seed
                    ),
                    **receipt_seed,
                }
            )
            connection.execute(
                """
                INSERT INTO tenant_audit_expirations (
                    brand_id, receipt_id, manifest_checksum, receipt_json,
                    expired_before, expired_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    receipt["audit_expiration_receipt_id"],
                    receipt["audit_expiration_manifest_checksum"],
                    canonical_bytes(receipt).decode("utf-8"),
                    receipt["expired_before"],
                    receipt["expired_at"],
                ),
            )
            self._insert_audit(
                connection,
                principal,
                "authority.audit_events.expired",
                receipt["audit_expiration_receipt_id"],
            )
            connection.commit()
            return receipt
        except (AuthorizationError, ContractError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not expire audit events") from exc
        finally:
            connection.close()

    def audit_expiration_receipt(
        self,
        principal: Principal,
        receipt_id: str,
    ) -> dict[str, Any]:
        self._require_audit_reader(principal)
        connection = self._database.connect()
        try:
            row = connection.execute(
                """
                SELECT receipt_id, manifest_checksum, receipt_json,
                       expired_before, expired_at
                FROM tenant_audit_expirations
                WHERE brand_id = ? AND receipt_id = ?
                """,
                (principal.brand_id, receipt_id),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            receipt = self._validated_audit_expiration_receipt(
                json.loads(row[2]), principal.brand_id
            )
            if (
                receipt["audit_expiration_receipt_id"] != row[0]
                or receipt["audit_expiration_receipt_id"] != receipt_id
                or receipt["audit_expiration_manifest_checksum"] != row[1]
                or receipt["expired_before"] != row[3]
                or receipt["expired_at"] != row[4]
            ):
                raise PlatformAdapterError(
                    "audit expiration evidence is invalid"
                )
            return receipt
        except (
            AuthorizationError,
            ContractError,
            KeyError,
            PlatformAdapterError,
        ):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError(
                "could not read audit expiration evidence"
            ) from exc
        finally:
            connection.close()

    def _current_audit_retention_policy(
        self,
        connection: sqlite3.Connection,
        brand_id: str,
        *,
        anchor_connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT revision, record_json, checksum, effective_at
            FROM tenant_audit_retention_policies
            WHERE brand_id = ? ORDER BY revision
            """,
            (brand_id,),
        ).fetchall()
        if not rows:
            raise ContractError("audit retention policy is not configured")
        owns_anchor_connection = anchor_connection is None
        try:
            if anchor_connection is None:
                anchor_connection = self._deletion_ledger.connect()
            anchors = self._deletion_ledger.audit_retention_anchors(
                anchor_connection, brand_id
            )
            if self._deletion_ledger.audit_retention_intents(
                anchor_connection, brand_id
            ):
                raise ArtifactStoreError("audit retention recovery is pending")
        except ArtifactStoreError as exc:
            raise PlatformAdapterError(
                "audit retention policy is invalid"
            ) from exc
        finally:
            if owns_anchor_connection and anchor_connection is not None:
                anchor_connection.close()
        if len(anchors) != len(rows):
            raise PlatformAdapterError("audit retention policy is invalid")
        previous_checksum: str | None = None
        current: dict[str, Any] | None = None
        for expected_revision, (row, anchor) in enumerate(
            zip(rows, anchors, strict=True), start=1
        ):
            policy = self._validated_audit_retention_policy(
                json.loads(row[1]),
                brand_id,
                self._audit_retention_authority,
            )
            if (
                row[0] != expected_revision
                or policy["revision"] != row[0]
                or policy["previous_checksum"] != previous_checksum
                or policy["content_checksum"] != row[2]
                or policy["effective_at"] != row[3]
                or anchor
                != (
                    policy["revision"],
                    policy["content_checksum"],
                    policy["minimum_retention_days"],
                    policy["effective_at"],
                )
            ):
                raise PlatformAdapterError("audit retention policy is invalid")
            previous_checksum = policy["content_checksum"]
            current = policy
        if current is None:  # pragma: no cover - guarded by the non-empty rows check
            raise PlatformAdapterError("audit retention policy is invalid")
        return current

    @staticmethod
    def _validated_audit_retention_policy(
        policy: Mapping[str, Any],
        brand_id: str,
        authority: _FictionalAuditRetentionAuthority,
    ) -> dict[str, Any]:
        validated = copy.deepcopy(dict(policy))
        try:
            authority.verify(validated)
        except ContractError as exc:
            raise PlatformAdapterError(
                "audit retention policy is invalid"
            ) from exc
        if (
            set(validated)
            != {
                "schema_version",
                "artifact_type",
                "brand_id",
                "revision",
                "previous_checksum",
                "minimum_retention_days",
                "evidence_ref",
                "approved_by",
                "effective_at",
                "audit_retention_attestation",
                "content_checksum",
            }
            or validated.get("schema_version") != "1.0"
            or validated.get("artifact_type")
            != "tenant_audit_retention_policy"
            or validated.get("brand_id") != brand_id
            or isinstance(validated.get("revision"), bool)
            or not isinstance(validated.get("revision"), int)
            or validated["revision"] < 1
            or isinstance(validated.get("minimum_retention_days"), bool)
            or not isinstance(validated.get("minimum_retention_days"), int)
            or not 1 <= validated["minimum_retention_days"] <= 3650
            or not isinstance(validated.get("evidence_ref"), str)
            or not validated["evidence_ref"].startswith("evidence://")
            or not isinstance(validated.get("approved_by"), str)
            or not validated["approved_by"]
            or not isinstance(validated.get("effective_at"), str)
            or (
                validated["revision"] == 1
                and validated.get("previous_checksum") is not None
            )
            or (
                validated["revision"] > 1
                and (
                    not isinstance(validated.get("previous_checksum"), str)
                    or not validated["previous_checksum"].startswith("sha256:")
                )
            )
        ):
            raise PlatformAdapterError("audit retention policy is invalid")
        parse_time(validated["effective_at"])
        return validated

    @staticmethod
    def _validated_audit_expiration_receipt(
        receipt: Mapping[str, Any],
        brand_id: str,
    ) -> dict[str, Any]:
        validated = copy.deepcopy(dict(receipt))
        verify_record(validated)
        string_fields = {
            "audit_expiration_receipt_id",
            "policy_checksum",
            "audit_expiration_manifest_checksum",
            "expired_before",
            "events_checksum",
            "evidence_reference",
            "expired_at",
        }
        if (
            set(validated)
            != {
                "schema_version",
                "artifact_type",
                "audit_expiration_receipt_id",
                "brand_id",
                "policy_revision",
                "policy_checksum",
                "audit_expiration_manifest_checksum",
                "expired_before",
                "event_count",
                "events_checksum",
                "evidence_reference",
                "expired_at",
                "content_checksum",
            }
            or validated.get("schema_version") != "1.0"
            or validated.get("artifact_type")
            != "tenant_audit_expiration_receipt"
            or validated.get("brand_id") != brand_id
            or any(
                not isinstance(validated.get(field), str)
                or not validated[field]
                for field in string_fields
            )
            or isinstance(validated.get("policy_revision"), bool)
            or not isinstance(validated.get("policy_revision"), int)
            or validated["policy_revision"] < 1
            or isinstance(validated.get("event_count"), bool)
            or not isinstance(validated.get("event_count"), int)
            or validated["event_count"] < 1
            or not validated["policy_checksum"].startswith("sha256:")
            or not validated["audit_expiration_manifest_checksum"].startswith(
                "sha256:"
            )
            or not validated["events_checksum"].startswith("sha256:")
            or not validated["evidence_reference"].startswith("hmac-sha256:")
            or len(validated["evidence_reference"]) != 76
            or any(
                character not in "0123456789abcdef"
                for character in validated["evidence_reference"][12:]
            )
        ):
            raise PlatformAdapterError("audit expiration evidence is invalid")
        seed = {
            key: validated[key]
            for key in (
                "brand_id",
                "policy_revision",
                "policy_checksum",
                "audit_expiration_manifest_checksum",
                "expired_before",
                "event_count",
                "events_checksum",
                "evidence_reference",
                "expired_at",
            )
        }
        if validated["audit_expiration_receipt_id"] != canonical_checksum(seed):
            raise PlatformAdapterError("audit expiration evidence is invalid")
        parse_time(validated["expired_before"])
        parse_time(validated["expired_at"])
        return validated

    @classmethod
    def _audit_expiration_state(
        cls,
        connection: sqlite3.Connection,
        brand_id: str,
        policy: Mapping[str, Any],
        expired_before: datetime,
    ) -> tuple[dict[str, Any], list[int]]:
        rows = cls._audit_rows(connection, brand_id)
        eligible = [
            (sequence, event, created_at)
            for sequence, event, created_at in rows
            if parse_time(created_at) < expired_before
        ]
        event_checksums = [
            canonical_checksum(
                {
                    "event_checksum": event["content_checksum"],
                    "created_at": created_at,
                }
            )
            for _sequence, event, created_at in eligible
        ]
        return (
            {
                "brand_id": brand_id,
                "policy_revision": policy["revision"],
                "policy_checksum": policy["content_checksum"],
                "expired_before": expired_before.isoformat(),
                "event_count": len(eligible),
                "events_checksum": canonical_checksum(event_checksums),
            },
            [sequence for sequence, _event, _created_at in eligible],
        )

    @staticmethod
    def _audit_rows(
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> list[tuple[int, dict[str, Any], str]]:
        rows = connection.execute(
            """
            SELECT sequence, event_json, created_at FROM platform_audit
            WHERE brand_id = ? ORDER BY sequence
            """,
            (brand_id,),
        ).fetchall()
        validated: list[tuple[int, dict[str, Any], str]] = []
        for sequence, event_json, created_at in rows:
            event = json.loads(event_json)
            verify_record(event)
            if (
                event.get("artifact_type") != "platform_audit_event"
                or event.get("brand_id") != brand_id
                or event.get("created_at") != created_at
                or not isinstance(event.get("event_type"), str)
                or not event["event_type"]
            ):
                raise PlatformAdapterError("platform audit event is invalid")
            parse_time(created_at)
            validated.append((int(sequence), event, str(created_at)))
        return validated

    @staticmethod
    def _require_audit_reader(principal: Principal) -> None:
        if principal.role_id not in {
            "agency-director",
            "platform-assurance-reviewer",
        }:
            raise AuthorizationError("role cannot inspect audit governance")

    def _mutate_task(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        event_type: str,
        mutate: Callable[[sqlite3.Connection, dict[str, Any]], None],
    ) -> dict[str, Any]:
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_current_task(
                connection, principal.brand_id, issue_id
            )
            if current["content_checksum"] != expected_checksum:
                raise ContractError("Paperclip task checksum drift")
            next_record = copy.deepcopy(current)
            next_record.pop("content_checksum")
            next_record["version"] += 1
            next_record["previous_checksum"] = expected_checksum
            next_record["updated_at"] = _authority_now(self._clock).isoformat()
            mutate(connection, next_record)
            next_record = finalize_record(next_record)
            _validate_task(next_record)
            self._insert_task_version(connection, next_record)
            self._insert_audit(connection, principal, event_type, issue_id)
            connection.commit()
            return next_record
        except (AuthorizationError, ContractError, KeyError, PlatformAdapterError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise PlatformAdapterError("could not update Paperclip task") from exc
        finally:
            connection.close()

    @staticmethod
    def _read_current_task(
        connection: sqlite3.Connection, brand_id: str, issue_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT record_json FROM paperclip_task_versions
            WHERE brand_id = ? AND issue_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (brand_id, issue_id),
        ).fetchone()
        if row is None:
            raise KeyError(issue_id)
        record = json.loads(row[0])
        _validate_task(record)
        return record

    @staticmethod
    def _insert_task_version(
        connection: sqlite3.Connection, record: Mapping[str, Any]
    ) -> None:
        connection.execute(
            """
            INSERT INTO paperclip_task_versions (
                brand_id, issue_id, version, record_json, checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["brand_id"],
                record["paperclip_issue_id"],
                record["version"],
                canonical_bytes(record).decode("utf-8"),
                record["content_checksum"],
                record["updated_at"],
            ),
        )

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        principal: Principal,
        event_type: str,
        subject_id: str,
    ) -> None:
        timestamp = utc_now()
        event = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "platform_audit_event",
                "brand_id": principal.brand_id,
                "actor_id": principal.actor_id,
                "role_id": principal.role_id,
                "event_type": event_type,
                "subject_id": subject_id,
                "created_at": timestamp,
            }
        )
        connection.execute(
            """
            INSERT INTO platform_audit (brand_id, event_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                principal.brand_id,
                canonical_bytes(event).decode("utf-8"),
                timestamp,
            ),
        )

    @staticmethod
    def _require_director(principal: Principal) -> None:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may mutate Paperclip")

    @staticmethod
    def _require_dependencies_done(
        connection: sqlite3.Connection, task: Mapping[str, Any]
    ) -> None:
        for dependency_id in task["dependencies"]:
            dependency = _AuthorityPaperclipAdapter._read_current_task(
                connection, task["brand_id"], dependency_id
            )
            if dependency["status"] != "done":
                raise ContractError(
                    f"Paperclip dependency {dependency_id!r} is not done"
                )

    @staticmethod
    def _read_active_approver_policy(
        connection: sqlite3.Connection, brand_id: str, policy_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT record_json FROM paperclip_approver_policies
            WHERE brand_id = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (brand_id,),
        ).fetchone()
        if row is None:
            raise ContractError("Paperclip approver policy is missing")
        policy = json.loads(row[0])
        _validate_approver_policy(policy)
        if policy["brand_id"] != brand_id or policy["policy_id"] != policy_id:
            raise ContractError("stored approver policy binding is invalid")
        return policy

    def _require_valid_approval(
        self,
        connection: sqlite3.Connection,
        task: Mapping[str, Any],
        approval_id: str | None,
        task_checksum: str,
    ) -> None:
        if not approval_id:
            raise ContractError("Paperclip task requires an approval")
        row = connection.execute(
            """
            SELECT record_json FROM paperclip_approvals
            WHERE brand_id = ? AND approval_id = ?
            """,
            (task["brand_id"], approval_id),
        ).fetchone()
        if row is None:
            raise ContractError("Paperclip approval is missing")
        approval = json.loads(row[0])
        self._approval_authority.verify(approval)
        policy_id = approval.get("policy_id")
        if not isinstance(policy_id, str) or not policy_id:
            raise ContractError("Paperclip approval has no approver policy binding")
        policy = self._read_active_approver_policy(
            connection, task["brand_id"], policy_id
        )
        authority_time = _authority_now(self._clock)
        if (
            approval.get("artifact_type") != "paperclip_task_approval"
            or approval.get("brand_id") != task["brand_id"]
            or approval.get("paperclip_issue_id") != task["paperclip_issue_id"]
            or approval.get("task_checksum") != task_checksum
            or approval.get("decision") != "APPROVED"
            or approval.get("authority_role") != "human-approver"
            or approval.get("policy_revision") != policy["revision"]
            or approval.get("policy_checksum") != policy["content_checksum"]
            or approval.get("approver_id") not in policy["permitted_approver_ids"]
            or parse_time(approval["decided_at"])
            < parse_time(policy["effective_at"])
            or parse_time(approval["decided_at"]) > authority_time
            or parse_time(approval["expires_at"]) <= authority_time
        ):
            raise ContractError("Paperclip approval is invalid, stale, or rejected")

    @staticmethod
    def _require_evidence(
        connection: sqlite3.Connection,
        task: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> None:
        for evidence_id in evidence_refs:
            row = connection.execute(
                """
                SELECT 1 FROM tenant_evidence
                WHERE brand_id = ? AND evidence_id = ? AND issue_id = ?
                """,
                (
                    task["brand_id"],
                    evidence_id,
                    task["paperclip_issue_id"],
                ),
            ).fetchone()
            if row is None:
                raise ContractError(
                    f"Paperclip closure evidence {evidence_id!r} is missing"
                )


class FictionalBuzzAdapter:
    """Typed local Buzz collaboration surface with Paperclip write-back only."""

    def __init__(
        self,
        paperclip: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._paperclip = paperclip
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._contexts: dict[tuple[str, str], dict[str, Any]] = {}

    def post_context(
        self, principal: Principal, packet: Mapping[str, Any]
    ) -> dict[str, Any]:
        _validate_buzz_context(packet)
        if packet["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant Buzz context denied")
        if packet["created_by"] != principal.actor_id:
            raise AuthorizationError("Buzz context author does not match actor")
        if parse_time(packet["deadline"]) <= _authority_now(self._clock):
            raise ContractError("Buzz context deadline has expired")
        task = self._paperclip.get_task(principal, packet["paperclip_issue_id"])
        if task["campaign_id"] != packet["campaign_id"]:
            raise ContractError("Buzz context campaign does not match Paperclip")
        self._paperclip.record_buzz_context(principal, packet)
        key = (principal.brand_id, packet["context_id"])
        existing = self._contexts.get(key)
        record = copy.deepcopy(dict(packet))
        if existing is not None and existing != record:
            raise ContractError("Buzz context is immutable")
        self._contexts[key] = record
        return copy.deepcopy(record)

    def collect_decision(
        self,
        principal: Principal,
        *,
        context_id: str,
        decision_id: str,
        summary: str,
        source_event_ids: Sequence[str],
    ) -> dict[str, Any]:
        if principal.role_id != "agency-director":
            raise AuthorizationError(
                "only the agency director may write a Buzz decision to Paperclip"
            )
        key = (principal.brand_id, context_id)
        context = self._contexts.get(key)
        if context is None:
            context = self._paperclip.get_buzz_context(principal, context_id)
            self._contexts[key] = copy.deepcopy(context)
        if self._paperclip.get_buzz_context_state(principal, context_id) != "open":
            raise ContractError("Buzz context is already archived")
        if not decision_id:
            raise ContractError("Buzz decision_id is required")
        if not summary or not source_event_ids or len(set(source_event_ids)) != len(
            source_event_ids
        ):
            raise ContractError("Buzz decision needs a summary and unique event IDs")
        authority_time = _authority_now(self._clock)
        if parse_time(context["deadline"]) <= authority_time:
            raise ContractError("Buzz context deadline has expired")
        decision = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "buzz_decision_summary",
                "decision_id": decision_id,
                "brand_id": principal.brand_id,
                "campaign_id": context["campaign_id"],
                "paperclip_issue_id": context["paperclip_issue_id"],
                "context_id": context_id,
                "context_checksum": context["content_checksum"],
                "summary": summary,
                "source_event_ids": list(source_event_ids),
                "recorded_by": principal.actor_id,
                "recorded_at": authority_time.isoformat(),
            }
        )
        return self._paperclip.record_buzz_decision(principal, decision)

    def archive(self, principal: Principal, context_id: str) -> None:
        self._paperclip.archive_buzz_context(principal, context_id)


class _AuthorityTenantEvidenceStore:
    """Persistent immutable evidence partitioned by brand and Paperclip issue."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_ADAPTER_TOKEN:
            raise EvidenceStoreError(
                "tenant evidence authority construction is denied"
            )
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=EvidenceStoreError,
        )

    def put(self, principal: Principal, evidence: Mapping[str, Any]) -> str:
        _validate_evidence(evidence)
        if principal.role_id not in EVIDENCE_WRITERS:
            raise AuthorizationError("role cannot write tenant evidence")
        if evidence["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant evidence write denied")
        if evidence["created_by"] != principal.actor_id:
            raise AuthorizationError("evidence author does not match actor")
        record = copy.deepcopy(dict(evidence))
        evidence_id = record["evidence_id"]
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            issue = connection.execute(
                """
                SELECT 1 FROM paperclip_task_versions
                WHERE brand_id = ? AND issue_id = ?
                LIMIT 1
                """,
                (principal.brand_id, record["paperclip_issue_id"]),
            ).fetchone()
            if issue is None:
                raise ContractError("evidence must reference an existing Paperclip task")
            row = connection.execute(
                """
                SELECT record_json FROM tenant_evidence
                WHERE brand_id = ? AND evidence_id = ?
                """,
                (principal.brand_id, evidence_id),
            ).fetchone()
            if row is not None:
                if json.loads(row[0]) != record:
                    raise ContractError(f"evidence {evidence_id!r} is immutable")
                connection.commit()
                return evidence_id
            connection.execute(
                """
                INSERT INTO tenant_evidence (
                    brand_id, evidence_id, issue_id, record_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    evidence_id,
                    record["paperclip_issue_id"],
                    canonical_bytes(record).decode("utf-8"),
                    record["content_checksum"],
                    record["retrieved_at"],
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection, principal, "evidence.recorded", evidence_id
            )
            connection.commit()
            return evidence_id
        except (AuthorizationError, ContractError, EvidenceStoreError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise EvidenceStoreError("could not persist tenant evidence") from exc
        finally:
            connection.close()

    def get(self, principal: Principal, evidence_id: str) -> dict[str, Any]:
        connection = self._database.connect()
        try:
            row = connection.execute(
                """
                SELECT record_json FROM tenant_evidence
                WHERE brand_id = ? AND evidence_id = ?
                """,
                (principal.brand_id, evidence_id),
            ).fetchone()
            if row is None:
                raise KeyError(evidence_id)
            record = json.loads(row[0])
            _validate_evidence(record)
            return record
        except (KeyError, ContractError, EvidenceStoreError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise EvidenceStoreError("could not read tenant evidence") from exc
        finally:
            connection.close()

    def list_for_issue(
        self, principal: Principal, paperclip_issue_id: str
    ) -> list[dict[str, Any]]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                """
                SELECT record_json FROM tenant_evidence
                WHERE brand_id = ? AND issue_id = ?
                ORDER BY evidence_id
                """,
                (principal.brand_id, paperclip_issue_id),
            ).fetchall()
            records = [json.loads(row[0]) for row in rows]
            for record in records:
                _validate_evidence(record)
            return records
        except (ContractError, EvidenceStoreError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise EvidenceStoreError("could not list tenant evidence") from exc
        finally:
            connection.close()


class _AuthorityTenantArtifactStore:
    """Durable immutable artifact and learning records inside the authority."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime],
        recovery_authority: _FictionalRecoveryAuthority,
        deletion_ledger: _SQLiteArtifactDeletionLedger,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_ADAPTER_TOKEN:
            raise ArtifactStoreError(
                "tenant artifact authority construction is denied"
            )
        self._clock = clock
        self._recovery_authority = recovery_authority
        self._deletion_ledger = deletion_ledger
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=ArtifactStoreError,
        )

    @staticmethod
    def _record_id(record: Mapping[str, Any]) -> str:
        record_type = str(record.get("artifact_type"))
        field = RECORD_ID_FIELDS.get(record_type, "artifact_id")
        record_id = record.get(field)
        if not isinstance(record_id, str) or not record_id:
            raise ContractError("artifact record has no recognised identifier")
        return record_id

    @staticmethod
    def _can_read(principal: Principal, record_type: object) -> bool:
        allowed = ROLE_READS.get(principal.role_id, frozenset())
        return "*" in allowed or record_type in allowed

    def _begin_tenant_guard(
        self,
        brand_id: str,
        *,
        deleted_is_missing: bool = False,
    ) -> sqlite3.Connection:
        connection = self._deletion_ledger.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self._deletion_ledger.deleted_receipt(connection, brand_id) is not None:
                if deleted_is_missing:
                    raise KeyError(brand_id)
                raise AuthorizationError(
                    "deleted tenant artifact authority cannot be used"
                )
            return connection
        except (AuthorizationError, ArtifactStoreError, KeyError):
            _rollback(connection)
            connection.close()
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            connection.close()
            raise ArtifactStoreError(
                "could not consult artifact deletion ledger"
            ) from exc

    def put(self, principal: Principal, artifact: Mapping[str, Any]) -> str:
        verify_record(artifact)
        record = copy.deepcopy(dict(artifact))
        if record.get("brand_id") != principal.brand_id:
            raise AuthorizationError("cross-tenant artifact write denied")
        record_type = record.get("artifact_type")
        if not isinstance(record_type, str) or not record_type:
            raise ContractError("artifact type is invalid")
        if record_type not in ROLE_WRITES.get(principal.role_id, frozenset()):
            raise AuthorizationError("role cannot write this artifact type")
        if (
            record_type == "approval_record"
            and record.get("approver_id") != principal.actor_id
        ):
            raise AuthorizationError("approval signer does not match actor")
        record_id = self._record_id(record)
        record_json = canonical_bytes(record).decode("utf-8")
        ledger_connection = self._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT record_json FROM tenant_artifacts
                WHERE brand_id = ? AND record_id = ?
                """,
                (principal.brand_id, record_id),
            ).fetchone()
            if row is not None:
                if str(row[0]) != record_json:
                    raise ContractError(f"artifact {record_id!r} is immutable")
                connection.commit()
                ledger_connection.commit()
                return record_id
            stored_at = _authority_now(self._clock).isoformat()
            connection.execute(
                """
                INSERT INTO tenant_artifacts (
                    brand_id,
                    record_id,
                    artifact_type,
                    record_json,
                    actor_id,
                    role_id,
                    stored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    record_id,
                    record_type,
                    record_json,
                    principal.actor_id,
                    principal.role_id,
                    stored_at,
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection, principal, "artifact.recorded", record_id
            )
            connection.commit()
            ledger_connection.commit()
            return record_id
        except (AuthorizationError, ContractError, ArtifactStoreError):
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise ArtifactStoreError("could not persist tenant artifact") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def get(self, principal: Principal, record_id: str) -> dict[str, Any]:
        ledger_connection = self._begin_tenant_guard(
            principal.brand_id,
            deleted_is_missing=True,
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            row = connection.execute(
                """
                SELECT artifact_type, record_json FROM tenant_artifacts
                WHERE brand_id = ? AND record_id = ?
                """,
                (principal.brand_id, record_id),
            ).fetchone()
            if row is None:
                raise KeyError(record_id)
            if not self._can_read(principal, row[0]):
                raise AuthorizationError("role cannot read this artifact type")
            record = json.loads(row[1])
            verify_record(record)
            if (
                record.get("brand_id") != principal.brand_id
                or self._record_id(record) != record_id
                or record.get("artifact_type") != row[0]
            ):
                raise ArtifactStoreError("stored tenant artifact identity is invalid")
            ledger_connection.commit()
            return record
        except (KeyError, AuthorizationError, ContractError, ArtifactStoreError):
            _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(ledger_connection)
            raise ArtifactStoreError("could not read tenant artifact") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def active_learning(self, principal: Principal) -> list[dict[str, Any]]:
        if not self._can_read(principal, "learning_record"):
            raise AuthorizationError("role cannot read learning records")
        now = _authority_now(self._clock)
        ledger_connection = self._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            rows = connection.execute(
                """
                SELECT record_id, record_json FROM tenant_artifacts
                WHERE brand_id = ? AND artifact_type = 'learning_record'
                ORDER BY record_id
                """,
                (principal.brand_id,),
            ).fetchall()
            active: list[dict[str, Any]] = []
            for record_id, record_json in rows:
                record = json.loads(record_json)
                verify_record(record)
                if (
                    self._record_id(record) != record_id
                    or record.get("brand_id") != principal.brand_id
                    or record.get("artifact_type") != "learning_record"
                ):
                    raise ArtifactStoreError(
                        "stored learning record identity is invalid"
                    )
                if record.get("validation_status") != "validated":
                    continue
                if record.get("lifecycle_status") != "active":
                    continue
                if not record.get("evidence_refs"):
                    continue
                fresh_until = record.get("fresh_until")
                if not isinstance(fresh_until, str) or not fresh_until:
                    raise ArtifactStoreError("stored learning freshness is invalid")
                if parse_time(fresh_until) <= now:
                    continue
                active.append(record)
            ledger_connection.commit()
            return active
        except (AuthorizationError, ContractError, ArtifactStoreError):
            _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(ledger_connection)
            raise ArtifactStoreError("could not read active learning") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def export_tenant(self, principal: Principal) -> dict[str, Any]:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may export a tenant")
        ledger_connection = self._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            exported = self._export_from_connection(connection, principal.brand_id)
            exported["exported_at"] = _authority_now(self._clock).isoformat()
            exported["export_attestation"] = self._recovery_authority.attest(
                exported
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "artifact.tenant_exported",
                exported["export_checksum"],
            )
            connection.commit()
            ledger_connection.commit()
            return exported
        except (AuthorizationError, ContractError, ArtifactStoreError):
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise ArtifactStoreError("could not export tenant artifacts") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def restore_tenant(
        self, principal: Principal, tenant_export: Mapping[str, Any]
    ) -> int:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may restore a tenant")
        records, provenance = self._validate_export(principal, tenant_export)
        ledger_connection = self._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1 FROM tenant_artifacts
                WHERE brand_id = ? LIMIT 1
                """,
                (principal.brand_id,),
            ).fetchone()
            if existing is not None:
                raise ContractError("restore target tenant is not empty")
            for record_id in sorted(records):
                record = records[record_id]
                record_provenance = provenance[record_id]
                connection.execute(
                    """
                    INSERT INTO tenant_artifacts (
                        brand_id,
                        record_id,
                        artifact_type,
                        record_json,
                        actor_id,
                        role_id,
                        stored_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        principal.brand_id,
                        record_id,
                        record["artifact_type"],
                        canonical_bytes(record).decode("utf-8"),
                        record_provenance["actor_id"],
                        record_provenance["role_id"],
                        record_provenance["stored_at"],
                    ),
                )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "artifact.tenant_restored",
                tenant_export["export_checksum"],
            )
            connection.commit()
            ledger_connection.commit()
            return len(records)
        except (AuthorizationError, ContractError, ArtifactStoreError):
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise ArtifactStoreError("could not restore tenant artifacts") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def delete_tenant(
        self, principal: Principal, expected_export_checksum: str
    ) -> dict[str, Any]:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may delete a tenant")
        ledger_connection = self._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            exported = self._export_from_connection(connection, principal.brand_id)
            if exported["record_count"] < 1:
                raise ContractError("tenant has no artifacts to delete")
            if expected_export_checksum != exported["export_checksum"]:
                raise ContractError("tenant export checksum is stale or incorrect")
            queue_cancellation = connection.execute(
                """
                SELECT receipt_id, receipt_json FROM tenant_queue_cancellations
                WHERE brand_id = ?
                """,
                (principal.brand_id,),
            ).fetchone()
            if queue_cancellation is None:
                raise ContractError(
                    "tenant work queue must be cancelled before artifact deletion"
                )
            try:
                queue_cancellation_receipt = (
                    _AuthorityWorkQueue._validated_cancellation_receipt(
                        json.loads(queue_cancellation[1]), principal.brand_id
                    )
                )
            except WorkQueueError as exc:
                raise ArtifactStoreError(
                    "tenant queue cancellation evidence is invalid"
                ) from exc
            if (
                queue_cancellation_receipt["queue_cancellation_receipt_id"]
                != queue_cancellation[0]
            ):
                raise ContractError("tenant queue cancellation evidence is invalid")
            deleted_at = _authority_now(self._clock).isoformat()
            receipt_seed = {
                "brand_id": principal.brand_id,
                "export_checksum": expected_export_checksum,
                "record_count": exported["record_count"],
                "queue_cancellation_receipt_id": queue_cancellation_receipt[
                    "queue_cancellation_receipt_id"
                ],
                "requested_by": principal.actor_id,
                "deleted_at": deleted_at,
            }
            receipt_id = canonical_checksum(receipt_seed)
            receipt = finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_artifact_deletion_receipt",
                    "deletion_receipt_id": receipt_id,
                    **receipt_seed,
                }
            )
            self._deletion_ledger.insert_deletion(
                ledger_connection,
                brand_id=principal.brand_id,
                receipt=receipt,
            )
            ledger_connection.commit()
            audit_rows = connection.execute(
                """
                SELECT sequence, event_json FROM platform_audit
                WHERE brand_id = ?
                """,
                (principal.brand_id,),
            ).fetchall()
            for sequence, event_json in audit_rows:
                event = json.loads(event_json)
                if str(event.get("event_type", "")).startswith("artifact."):
                    connection.execute(
                        "DELETE FROM platform_audit WHERE sequence = ?",
                        (sequence,),
                    )
            connection.execute(
                "DELETE FROM tenant_artifacts WHERE brand_id = ?",
                (principal.brand_id,),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "artifact.tenant_deleted",
                receipt_id,
            )
            connection.commit()
            return receipt
        except (AuthorizationError, ContractError, ArtifactStoreError):
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise ArtifactStoreError("could not delete tenant artifacts") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def deletion_receipt(
        self, principal: Principal, receipt_id: str
    ) -> dict[str, Any]:
        if principal.role_id not in {
            "agency-director",
            "platform-assurance-reviewer",
        }:
            raise AuthorizationError("role cannot read deletion evidence")
        connection = self._deletion_ledger.connect()
        try:
            row = self._deletion_ledger.deleted_receipt(
                connection, principal.brand_id
            )
            if row is None:
                raise KeyError(receipt_id)
            receipt = json.loads(row[1])
            verify_record(receipt)
            if (
                receipt.get("artifact_type")
                != "tenant_artifact_deletion_receipt"
                or receipt.get("deletion_receipt_id") != row[0]
                or receipt.get("deletion_receipt_id") != receipt_id
                or receipt.get("brand_id") != principal.brand_id
            ):
                raise ArtifactStoreError("stored deletion receipt is invalid")
            return receipt
        except (KeyError, AuthorizationError, ContractError, ArtifactStoreError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise ArtifactStoreError("could not read deletion evidence") from exc
        finally:
            connection.close()

    def _export_from_connection(
        self, connection: sqlite3.Connection, brand_id: str
    ) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT record_id, artifact_type, record_json, actor_id, role_id, stored_at
            FROM tenant_artifacts
            WHERE brand_id = ?
            ORDER BY record_id
            """,
            (brand_id,),
        ).fetchall()
        records: dict[str, dict[str, Any]] = {}
        provenance: dict[str, dict[str, str]] = {}
        for record_id, record_type, record_json, actor_id, role_id, stored_at in rows:
            record = json.loads(record_json)
            verify_record(record)
            if (
                record.get("brand_id") != brand_id
                or self._record_id(record) != record_id
                or record.get("artifact_type") != record_type
            ):
                raise ArtifactStoreError("stored tenant artifact identity is invalid")
            if not all(
                isinstance(value, str) and value
                for value in (actor_id, role_id, stored_at)
            ):
                raise ArtifactStoreError("stored artifact provenance is invalid")
            if record_type not in ROLE_WRITES.get(role_id, frozenset()):
                raise ArtifactStoreError("stored artifact provenance role is invalid")
            if (
                record_type == "approval_record"
                and record.get("approver_id") != actor_id
            ):
                raise ArtifactStoreError("stored approval provenance is invalid")
            parse_time(stored_at)
            records[record_id] = record
            provenance[record_id] = {
                "actor_id": actor_id,
                "role_id": role_id,
                "stored_at": stored_at,
            }
        payload = {
            "schema_version": "1.0",
            "brand_id": brand_id,
            "record_count": len(records),
            "records": records,
            "provenance": provenance,
        }
        return {**payload, "export_checksum": canonical_checksum(payload)}

    def _validate_export(
        self, principal: Principal, tenant_export: Mapping[str, Any]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
        exported = copy.deepcopy(dict(tenant_export))
        if set(exported) != {
            "schema_version",
            "brand_id",
            "record_count",
            "records",
            "provenance",
            "export_checksum",
            "exported_at",
            "export_attestation",
        }:
            raise ContractError("tenant artifact export shape is invalid")
        if exported["schema_version"] != "1.0":
            raise ContractError("tenant artifact export version is unsupported")
        if exported["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant artifact restore denied")
        exported_at = exported["exported_at"]
        if not isinstance(exported_at, str) or not exported_at:
            raise ContractError("tenant artifact export time is invalid")
        parse_time(exported_at)
        records = exported["records"]
        provenance = exported["provenance"]
        if not isinstance(records, dict) or not isinstance(provenance, dict):
            raise ContractError("tenant artifact export records are invalid")
        if set(records) != set(provenance):
            raise ContractError("tenant artifact export provenance is incomplete")
        record_count = exported["record_count"]
        if (
            isinstance(record_count, bool)
            or not isinstance(record_count, int)
            or record_count != len(records)
        ):
            raise ContractError("tenant artifact export count is invalid")
        payload = {
            key: exported[key]
            for key in (
                "schema_version",
                "brand_id",
                "record_count",
                "records",
                "provenance",
            )
        }
        if exported["export_checksum"] != canonical_checksum(payload):
            raise ContractError("tenant artifact export checksum is invalid")
        self._recovery_authority.verify(
            exported,
            exported["export_attestation"],
        )
        validated_records: dict[str, dict[str, Any]] = {}
        validated_provenance: dict[str, dict[str, str]] = {}
        for record_id, raw_record in records.items():
            if not isinstance(record_id, str) or not isinstance(raw_record, dict):
                raise ContractError("tenant artifact export record is invalid")
            verify_record(raw_record)
            if (
                raw_record.get("brand_id") != principal.brand_id
                or self._record_id(raw_record) != record_id
            ):
                raise ContractError("tenant artifact export identity is invalid")
            raw_provenance = provenance[record_id]
            if not isinstance(raw_provenance, dict) or set(raw_provenance) != {
                "actor_id",
                "role_id",
                "stored_at",
            }:
                raise ContractError("tenant artifact provenance is invalid")
            actor_id = raw_provenance.get("actor_id")
            role_id = raw_provenance.get("role_id")
            stored_at = raw_provenance.get("stored_at")
            if not all(isinstance(value, str) and value for value in (actor_id, role_id, stored_at)):
                raise ContractError("tenant artifact provenance is invalid")
            record_type = raw_record.get("artifact_type")
            if not isinstance(record_type, str) or not record_type:
                raise ContractError("tenant artifact type is invalid")
            if record_type not in ROLE_WRITES.get(role_id, frozenset()):
                raise ContractError("tenant artifact provenance role is invalid")
            if (
                raw_record.get("artifact_type") == "approval_record"
                and raw_record.get("approver_id") != actor_id
            ):
                raise ContractError("tenant approval provenance is invalid")
            parse_time(stored_at)
            validated_records[record_id] = copy.deepcopy(raw_record)
            validated_provenance[record_id] = {
                "actor_id": actor_id,
                "role_id": role_id,
                "stored_at": stored_at,
            }
        return validated_records, validated_provenance


class _AuthorityTenantRecovery:
    """Attested full-tenant export and empty-target recovery inside the authority."""

    _TABLES: dict[str, tuple[tuple[str, ...], str]] = {
        "paperclip_task_versions": (
            (
                "brand_id",
                "issue_id",
                "version",
                "record_json",
                "checksum",
                "created_at",
            ),
            "issue_id, version",
        ),
        "paperclip_approver_policies": (
            (
                "brand_id",
                "policy_id",
                "revision",
                "record_json",
                "checksum",
                "created_at",
            ),
            "policy_id, revision",
        ),
        "paperclip_approvals": (
            (
                "brand_id",
                "approval_id",
                "issue_id",
                "task_checksum",
                "record_json",
                "created_at",
            ),
            "approval_id",
        ),
        "paperclip_buzz_contexts": (
            (
                "brand_id",
                "context_id",
                "issue_id",
                "record_json",
                "checksum",
                "state",
                "created_at",
            ),
            "context_id",
        ),
        "paperclip_buzz_decisions": (
            (
                "brand_id",
                "decision_id",
                "issue_id",
                "context_checksum",
                "record_json",
                "created_at",
            ),
            "decision_id",
        ),
        "tenant_evidence": (
            (
                "brand_id",
                "evidence_id",
                "issue_id",
                "record_json",
                "checksum",
                "created_at",
            ),
            "evidence_id",
        ),
        "tenant_artifacts": (
            (
                "brand_id",
                "record_id",
                "artifact_type",
                "record_json",
                "actor_id",
                "role_id",
                "stored_at",
            ),
            "record_id",
        ),
        "platform_work_queue": (
            (
                "brand_id",
                "work_item_id",
                "work_json",
                "work_checksum",
                "work_kind",
                "worker_role",
                "state",
                "attempt_count",
                "max_attempts",
                "next_attempt_at",
                "leased_at",
                "lease_owner",
                "lease_token_hash",
                "lease_expires_at",
                "heartbeat_at",
                "error_classes_json",
                "disposition_json",
                "completed_at",
                "created_at",
                "updated_at",
            ),
            "work_item_id",
        ),
        "tenant_queue_cancellations": (
            (
                "brand_id",
                "receipt_id",
                "evidence_ref",
                "receipt_json",
                "cancelled_at",
            ),
            "brand_id",
        ),
        "tenant_audit_retention_policies": (
            (
                "brand_id",
                "revision",
                "record_json",
                "checksum",
                "effective_at",
            ),
            "revision",
        ),
        "tenant_audit_expirations": (
            (
                "brand_id",
                "receipt_id",
                "manifest_checksum",
                "receipt_json",
                "expired_before",
                "expired_at",
            ),
            "expired_at, receipt_id",
        ),
        # SQLite's audit sequence is authority-global. Logical recovery preserves
        # the ordered event records and lets the target allocate safe new values.
        "platform_audit": (
            ("brand_id", "event_json", "created_at"),
            "sequence",
        ),
    }

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float,
        clock: Callable[[], datetime],
        recovery_authority: _FictionalRecoveryAuthority,
        audit_retention_authority: _FictionalAuditRetentionAuthority,
        deletion_ledger: _SQLiteArtifactDeletionLedger,
        artifacts: _AuthorityTenantArtifactStore,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_ADAPTER_TOKEN:
            raise PlatformAdapterError("tenant recovery construction is denied")
        self._clock = clock
        self._recovery_authority = recovery_authority
        self._audit_retention_authority = audit_retention_authority
        self._deletion_ledger = deletion_ledger
        self._artifacts = artifacts
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=PlatformAdapterError,
        )

    def export_tenant(self, principal: Principal) -> dict[str, Any]:
        self._require_director(principal)
        ledger_connection = self._artifacts._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            tables = self._snapshot(
                connection,
                ledger_connection,
                principal.brand_id,
            )
            table_row_counts = {
                table: len(rows) for table, rows in sorted(tables.items())
            }
            payload = {
                "schema_version": "1.0",
                "artifact_type": "tenant_authority_export",
                "brand_id": principal.brand_id,
                "table_row_counts": table_row_counts,
                "tables": tables,
            }
            exported = {
                **payload,
                "export_checksum": canonical_checksum(payload),
                "exported_at": _authority_now(self._clock).isoformat(),
            }
            exported["export_attestation"] = self._recovery_authority.attest(
                exported
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "authority.tenant_exported",
                exported["export_checksum"],
            )
            connection.commit()
            ledger_connection.commit()
            return exported
        except (AuthorizationError, ContractError, PlatformAdapterError):
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise PlatformAdapterError("could not export tenant authority") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def restore_tenant(
        self,
        principal: Principal,
        tenant_export: Mapping[str, Any],
    ) -> dict[str, int]:
        self._require_director(principal)
        ledger_connection = self._artifacts._begin_tenant_guard(principal.brand_id)
        connection: sqlite3.Connection | None = None
        try:
            tables = self._validated_export(
                principal,
                tenant_export,
                ledger_connection,
            )
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            for table in self._TABLES:
                existing = connection.execute(
                    f"SELECT 1 FROM {table} WHERE brand_id = ? LIMIT 1",
                    (principal.brand_id,),
                ).fetchone()
                if existing is not None:
                    raise ContractError("authority restore target tenant is not empty")
            for table, (columns, _order_by) in self._TABLES.items():
                placeholders = ", ".join("?" for _column in columns)
                column_sql = ", ".join(columns)
                for row in tables[table]:
                    connection.execute(
                        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
                        tuple(row[column] for column in columns),
                    )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "authority.tenant_restored",
                str(tenant_export["export_checksum"]),
            )
            connection.commit()
            ledger_connection.commit()
            return {table: len(rows) for table, rows in sorted(tables.items())}
        except (AuthorizationError, ContractError, PlatformAdapterError):
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            _rollback(ledger_connection)
            raise PlatformAdapterError("could not restore tenant authority") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        ledger_connection: sqlite3.Connection,
        brand_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table, (columns, order_by) in self._TABLES.items():
            rows = connection.execute(
                f"SELECT {', '.join(columns)} FROM {table} "
                f"WHERE brand_id = ? ORDER BY {order_by}",
                (brand_id,),
            ).fetchall()
            exported_rows = [dict(zip(columns, row, strict=True)) for row in rows]
            for row in exported_rows:
                self._validate_row(table, row, brand_id)
            tables[table] = exported_rows
        self._validate_audit_retention_anchors(
            ledger_connection,
            tables["tenant_audit_retention_policies"],
            brand_id,
        )
        return tables

    def _validated_export(
        self,
        principal: Principal,
        tenant_export: Mapping[str, Any],
        ledger_connection: sqlite3.Connection,
    ) -> dict[str, list[dict[str, Any]]]:
        exported = copy.deepcopy(dict(tenant_export))
        if set(exported) != {
            "schema_version",
            "artifact_type",
            "brand_id",
            "table_row_counts",
            "tables",
            "export_checksum",
            "exported_at",
            "export_attestation",
        }:
            raise ContractError("tenant authority export shape is invalid")
        if (
            exported["schema_version"] != "1.0"
            or exported["artifact_type"] != "tenant_authority_export"
        ):
            raise ContractError("tenant authority export version is unsupported")
        if exported["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant authority restore denied")
        if (
            not isinstance(exported["exported_at"], str)
            or not exported["exported_at"]
        ):
            raise ContractError("tenant authority export time is invalid")
        parse_time(exported["exported_at"])
        tables = exported["tables"]
        table_row_counts = exported["table_row_counts"]
        if (
            not isinstance(tables, dict)
            or set(tables) != set(self._TABLES)
            or not isinstance(table_row_counts, dict)
            or set(table_row_counts) != set(self._TABLES)
        ):
            raise ContractError("tenant authority export tables are invalid")
        validated_tables: dict[str, list[dict[str, Any]]] = {}
        for table, (columns, _order_by) in self._TABLES.items():
            rows = tables[table]
            count = table_row_counts[table]
            if (
                not isinstance(rows, list)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count != len(rows)
            ):
                raise ContractError("tenant authority export count is invalid")
            validated_rows: list[dict[str, Any]] = []
            for raw_row in rows:
                if not isinstance(raw_row, dict) or set(raw_row) != set(columns):
                    raise ContractError("tenant authority export row is invalid")
                self._validate_row(table, raw_row, principal.brand_id)
                validated_rows.append(copy.deepcopy(raw_row))
            validated_tables[table] = validated_rows
        self._validate_audit_retention_anchors(
            ledger_connection,
            validated_tables["tenant_audit_retention_policies"],
            principal.brand_id,
        )
        payload = {
            key: exported[key]
            for key in (
                "schema_version",
                "artifact_type",
                "brand_id",
                "table_row_counts",
                "tables",
            )
        }
        if exported["export_checksum"] != canonical_checksum(payload):
            raise ContractError("tenant authority export checksum is invalid")
        self._recovery_authority.verify(
            exported,
            exported["export_attestation"],
        )
        return validated_tables

    def _validate_audit_retention_anchors(
        self,
        ledger_connection: sqlite3.Connection,
        rows: Sequence[Mapping[str, Any]],
        brand_id: str,
    ) -> None:
        try:
            anchors = self._deletion_ledger.audit_retention_anchors(
                ledger_connection, brand_id
            )
        except ArtifactStoreError as exc:
            raise PlatformAdapterError(
                "tenant authority audit retention anchor is invalid"
            ) from exc
        if len(anchors) != len(rows):
            raise PlatformAdapterError(
                "tenant authority audit retention anchor is invalid"
            )
        for row, anchor in zip(rows, anchors, strict=True):
            policy = json.loads(str(row["record_json"]))
            if anchor != (
                policy["revision"],
                policy["content_checksum"],
                policy["minimum_retention_days"],
                policy["effective_at"],
            ):
                raise PlatformAdapterError(
                    "tenant authority audit retention anchor is invalid"
                )

    def _validate_row(
        self,
        table: str,
        row: Mapping[str, Any],
        brand_id: str,
    ) -> None:
        if row.get("brand_id") != brand_id:
            raise ContractError("tenant authority export row tenant is invalid")
        decoded: dict[str, Any] = {}
        for column, value in row.items():
            if not column.endswith("_json") or value is None:
                continue
            if not isinstance(value, str):
                raise ContractError("tenant authority export JSON is invalid")
            parsed = json.loads(value)
            if canonical_bytes(parsed).decode("utf-8") != value:
                raise ContractError("tenant authority export JSON is not canonical")
            decoded[column] = parsed
            if isinstance(parsed, dict):
                verify_record(parsed)
                if parsed.get("brand_id") != brand_id:
                    raise ContractError(
                        "tenant authority export record tenant is invalid"
                    )
            elif (
                column not in {"error_classes_json", "disposition_json"}
                or not isinstance(parsed, list)
            ):
                raise ContractError("tenant authority export JSON shape is invalid")

        record = decoded.get("record_json")
        work = decoded.get("work_json")
        receipt = decoded.get("receipt_json")
        event = decoded.get("event_json")
        required_mapping = {
            "paperclip_task_versions": record,
            "paperclip_approver_policies": record,
            "paperclip_approvals": record,
            "paperclip_buzz_contexts": record,
            "paperclip_buzz_decisions": record,
            "tenant_evidence": record,
            "tenant_artifacts": record,
            "platform_work_queue": work,
            "tenant_queue_cancellations": receipt,
            "tenant_audit_retention_policies": record,
            "tenant_audit_expirations": receipt,
            "platform_audit": event,
        }[table]
        if not isinstance(required_mapping, dict):
            raise ContractError("tenant authority export record shape is invalid")
        if table == "paperclip_task_versions" and (
            record.get("paperclip_issue_id") != row.get("issue_id")
            or record.get("version") != row.get("version")
            or record.get("content_checksum") != row.get("checksum")
        ):
            raise ContractError("tenant authority task row is invalid")
        if table == "paperclip_approver_policies" and (
            record.get("policy_id") != row.get("policy_id")
            or record.get("revision") != row.get("revision")
            or record.get("content_checksum") != row.get("checksum")
        ):
            raise ContractError("tenant authority policy row is invalid")
        if table == "paperclip_approvals" and (
            record.get("approval_id") != row.get("approval_id")
            or record.get("paperclip_issue_id") != row.get("issue_id")
            or record.get("task_checksum") != row.get("task_checksum")
        ):
            raise ContractError("tenant authority approval row is invalid")
        if table == "paperclip_buzz_contexts" and (
            record.get("context_id") != row.get("context_id")
            or record.get("paperclip_issue_id") != row.get("issue_id")
            or record.get("content_checksum") != row.get("checksum")
            or row.get("state") not in {"open", "archived"}
        ):
            raise ContractError("tenant authority Buzz context row is invalid")
        if table == "paperclip_buzz_decisions" and (
            record.get("decision_id") != row.get("decision_id")
            or record.get("paperclip_issue_id") != row.get("issue_id")
            or record.get("context_checksum") != row.get("context_checksum")
        ):
            raise ContractError("tenant authority Buzz decision row is invalid")
        if table == "tenant_evidence" and (
            record.get("evidence_id") != row.get("evidence_id")
            or record.get("paperclip_issue_id") != row.get("issue_id")
            or record.get("content_checksum") != row.get("checksum")
        ):
            raise ContractError("tenant authority evidence row is invalid")
        if table == "tenant_artifacts" and (
            _AuthorityTenantArtifactStore._record_id(record) != row.get("record_id")
            or record.get("artifact_type") != row.get("artifact_type")
        ):
            raise ContractError("tenant authority artifact row is invalid")
        if table == "platform_work_queue" and (
            work.get("work_item_id") != row.get("work_item_id")
            or work.get("content_checksum") != row.get("work_checksum")
            or work.get("work_kind") != row.get("work_kind")
            or work.get("worker_role") != row.get("worker_role")
            or work.get("max_attempts") != row.get("max_attempts")
            or row.get("state") not in WORK_QUEUE_STATES
        ):
            raise ContractError("tenant authority queue row is invalid")
        if table == "tenant_queue_cancellations":
            validated_receipt = _AuthorityWorkQueue._validated_cancellation_receipt(
                receipt, brand_id
            )
            if (
                validated_receipt["queue_cancellation_receipt_id"]
                != row.get("receipt_id")
                or validated_receipt["evidence_ref"] != row.get("evidence_ref")
                or validated_receipt["cancelled_at"] != row.get("cancelled_at")
            ):
                raise ContractError(
                    "tenant authority queue cancellation row is invalid"
                )
        if table == "tenant_audit_retention_policies":
            validated_policy = (
                _AuthorityPaperclipAdapter._validated_audit_retention_policy(
                    record,
                    brand_id,
                    self._audit_retention_authority,
                )
            )
            if (
                validated_policy["revision"] != row.get("revision")
                or validated_policy["content_checksum"] != row.get("checksum")
                or validated_policy["effective_at"] != row.get("effective_at")
            ):
                raise ContractError(
                    "tenant authority audit retention policy row is invalid"
                )
        if table == "tenant_audit_expirations":
            validated_expiration = (
                _AuthorityPaperclipAdapter._validated_audit_expiration_receipt(
                    receipt, brand_id
                )
            )
            if (
                validated_expiration["audit_expiration_receipt_id"]
                != row.get("receipt_id")
                or validated_expiration["audit_expiration_manifest_checksum"]
                != row.get("manifest_checksum")
                or validated_expiration["expired_before"]
                != row.get("expired_before")
                or validated_expiration["expired_at"] != row.get("expired_at")
            ):
                raise ContractError(
                    "tenant authority audit expiration row is invalid"
                )
        if table == "platform_audit" and event.get("created_at") != row.get(
            "created_at"
        ):
            raise ContractError("tenant authority audit row is invalid")

    @staticmethod
    def _require_director(principal: Principal) -> None:
        if principal.role_id != "agency-director":
            raise AuthorizationError(
                "only the agency director may export or restore a tenant authority"
            )


class _AuthorityTenantOffboarding:
    """Irreversible local tenant cleanup coordinated by the protected authority."""

    _TENANT_TABLES = (
        ("paperclip_task_versions", "issue_id, version"),
        ("paperclip_approver_policies", "policy_id, revision"),
        ("paperclip_approvals", "approval_id"),
        ("paperclip_buzz_contexts", "context_id"),
        ("paperclip_buzz_decisions", "decision_id"),
        ("tenant_evidence", "evidence_id"),
        ("tenant_artifacts", "record_id"),
        ("platform_work_queue", "work_item_id"),
        ("tenant_queue_cancellations", "brand_id"),
        ("tenant_audit_retention_policies", "revision"),
        ("tenant_audit_expirations", "expired_at, receipt_id"),
        ("platform_audit", "sequence"),
    )
    _DELETED_TABLES = (
        "paperclip_task_versions",
        "paperclip_approver_policies",
        "paperclip_approvals",
        "paperclip_buzz_contexts",
        "paperclip_buzz_decisions",
        "tenant_evidence",
        "tenant_artifacts",
        "platform_work_queue",
        "tenant_audit_retention_policies",
        "platform_audit",
    )

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float,
        clock: Callable[[], datetime],
        artifacts: _AuthorityTenantArtifactStore,
        deletion_ledger: _SQLiteArtifactDeletionLedger,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_ADAPTER_TOKEN:
            raise PlatformAdapterError("tenant offboarding construction is denied")
        self._clock = clock
        self._artifacts = artifacts
        self._deletion_ledger = deletion_ledger
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=PlatformAdapterError,
        )

    def prepare(self, principal: Principal) -> dict[str, Any]:
        self._require_director(principal)
        ledger_connection = self._deletion_ledger.connect()
        connection: sqlite3.Connection | None = None
        try:
            if (
                self._deletion_ledger.authority_offboarding_receipt(
                    ledger_connection, principal.brand_id
                )
                is not None
            ):
                raise ContractError("tenant authority is already offboarded")
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            queue_receipt = self._queue_cancellation_receipt(
                connection, principal.brand_id
            )
            manifest = self._manifest(
                connection,
                principal.brand_id,
                queue_receipt["queue_cancellation_receipt_id"],
            )
            connection.commit()
            return manifest
        except (AuthorizationError, ContractError, PlatformAdapterError):
            if connection is not None:
                _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            raise PlatformAdapterError(
                "could not prepare tenant authority offboarding"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def offboard(
        self,
        principal: Principal,
        *,
        expected_authority_manifest_checksum: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        self._require_director(principal)
        if (
            not isinstance(expected_authority_manifest_checksum, str)
            or not expected_authority_manifest_checksum.startswith("sha256:")
        ):
            raise ContractError("tenant authority manifest checksum is required")
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.startswith("evidence://")
            or len(evidence_ref) > 512
        ):
            raise ContractError("tenant authority offboarding evidence is required")
        ledger_connection = self._deletion_ledger.connect()
        connection: sqlite3.Connection | None = None
        ledger_committed = False
        try:
            ledger_connection.execute("BEGIN IMMEDIATE")
            existing = self._deletion_ledger.authority_offboarding_receipt(
                ledger_connection, principal.brand_id
            )
            if existing is not None:
                receipt = self._validated_receipt(
                    json.loads(existing[1]), principal.brand_id
                )
                if receipt["tenant_offboarding_receipt_id"] != existing[0]:
                    raise PlatformAdapterError(
                        "tenant offboarding evidence is invalid"
                    )
                if (
                    receipt["authority_manifest_checksum"]
                    != expected_authority_manifest_checksum
                    or receipt["evidence_ref"] != evidence_ref
                ):
                    raise ContractError("tenant authority offboarding is immutable")
                ledger_connection.commit()
                ledger_committed = True
                self._delete_tenant_rows(principal.brand_id)
                return receipt

            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            queue_receipt = self._queue_cancellation_receipt(
                connection, principal.brand_id
            )
            queue_receipt_id = queue_receipt["queue_cancellation_receipt_id"]
            manifest = self._manifest(
                connection, principal.brand_id, queue_receipt_id
            )
            if (
                manifest["authority_manifest_checksum"]
                != expected_authority_manifest_checksum
            ):
                raise ContractError("tenant authority manifest is stale or incorrect")

            exported = self._artifacts._export_from_connection(
                connection, principal.brand_id
            )
            now = _authority_now(self._clock).isoformat()
            deletion_row = self._deletion_ledger.deleted_receipt(
                ledger_connection, principal.brand_id
            )
            if deletion_row is None:
                deletion_seed = {
                    "brand_id": principal.brand_id,
                    "export_checksum": exported["export_checksum"],
                    "record_count": exported["record_count"],
                    "queue_cancellation_receipt_id": queue_receipt_id,
                    "requested_by": principal.actor_id,
                    "deleted_at": now,
                }
                deletion_receipt = finalize_record(
                    {
                        "schema_version": "1.0",
                        "artifact_type": "tenant_artifact_deletion_receipt",
                        "deletion_receipt_id": canonical_checksum(deletion_seed),
                        **deletion_seed,
                    }
                )
                self._deletion_ledger.insert_deletion(
                    ledger_connection,
                    brand_id=principal.brand_id,
                    receipt=deletion_receipt,
                )
            else:
                deletion_receipt = self._validated_artifact_deletion_receipt(
                    json.loads(deletion_row[1]),
                    principal.brand_id,
                    queue_receipt_id,
                )
                if deletion_receipt["deletion_receipt_id"] != deletion_row[0]:
                    raise PlatformAdapterError(
                        "artifact deletion evidence is invalid"
                    )

            row_counts = {
                table: manifest["tables"][table]["row_count"]
                for table in sorted(manifest["tables"])
            }
            receipt_seed = {
                "brand_id": principal.brand_id,
                "authority_manifest_checksum": expected_authority_manifest_checksum,
                "artifact_deletion_receipt_id": deletion_receipt[
                    "deletion_receipt_id"
                ],
                "queue_cancellation_receipt_id": queue_receipt_id,
                "manifest_table_row_counts": row_counts,
                "evidence_ref": evidence_ref,
                "requested_by": principal.actor_id,
                "offboarded_at": now,
            }
            receipt = finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_authority_offboarding_receipt",
                    "tenant_offboarding_receipt_id": canonical_checksum(
                        receipt_seed
                    ),
                    **receipt_seed,
                }
            )
            self._deletion_ledger.insert_authority_offboarding(
                ledger_connection,
                brand_id=principal.brand_id,
                receipt=receipt,
            )
            ledger_connection.commit()
            ledger_committed = True
            self._delete_rows(connection, principal.brand_id)
            connection.commit()
            return receipt
        except (AuthorizationError, ContractError, PlatformAdapterError):
            if connection is not None:
                _rollback(connection)
            if not ledger_committed:
                _rollback(ledger_connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            if connection is not None:
                _rollback(connection)
            if not ledger_committed:
                _rollback(ledger_connection)
            raise PlatformAdapterError("could not offboard tenant authority") from exc
        finally:
            if connection is not None:
                connection.close()
            ledger_connection.close()

    def receipt(
        self,
        principal: Principal,
        receipt_id: str,
    ) -> dict[str, Any]:
        if principal.role_id not in {
            "agency-director",
            "platform-assurance-reviewer",
        }:
            raise AuthorizationError("role cannot read tenant offboarding evidence")
        connection = self._deletion_ledger.connect()
        try:
            row = self._deletion_ledger.authority_offboarding_receipt(
                connection, principal.brand_id
            )
            if row is None:
                raise KeyError(receipt_id)
            receipt = self._validated_receipt(
                json.loads(row[1]), principal.brand_id
            )
            if receipt["tenant_offboarding_receipt_id"] != row[0]:
                raise PlatformAdapterError(
                    "tenant offboarding evidence is invalid"
                )
            if receipt["tenant_offboarding_receipt_id"] != receipt_id:
                raise KeyError(receipt_id)
            return receipt
        except (
            AuthorizationError,
            ContractError,
            KeyError,
            PlatformAdapterError,
        ):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise PlatformAdapterError(
                "could not read tenant offboarding evidence"
            ) from exc
        finally:
            connection.close()

    def is_offboarded(self, brand_id: str) -> bool:
        connection = self._deletion_ledger.connect()
        try:
            return (
                self._deletion_ledger.authority_offboarding_receipt(
                    connection, brand_id
                )
                is not None
            )
        except sqlite3.Error as exc:
            raise PlatformAdapterError(
                "could not consult tenant offboarding authority"
            ) from exc
        finally:
            connection.close()

    def _delete_tenant_rows(self, brand_id: str) -> None:
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._delete_rows(connection, brand_id)
            connection.commit()
        except (PlatformAdapterError, sqlite3.Error) as exc:
            _rollback(connection)
            raise PlatformAdapterError(
                "could not complete tenant authority cleanup"
            ) from exc
        finally:
            connection.close()

    @classmethod
    def _delete_rows(
        cls,
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> None:
        for table in cls._DELETED_TABLES:
            connection.execute(
                f"DELETE FROM {table} WHERE brand_id = ?",
                (brand_id,),
            )

    def _manifest(
        self,
        connection: sqlite3.Connection,
        brand_id: str,
        queue_cancellation_receipt_id: str,
    ) -> dict[str, Any]:
        tables: dict[str, dict[str, Any]] = {}
        for table, order_by in self._TENANT_TABLES:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE brand_id = ? ORDER BY {order_by}",
                (brand_id,),
            ).fetchall()
            row_checksums = [canonical_checksum(list(row)) for row in rows]
            tables[table] = {
                "row_count": len(rows),
                "rows_checksum": canonical_checksum(row_checksums),
            }
        state = {
            "brand_id": brand_id,
            "queue_cancellation_receipt_id": queue_cancellation_receipt_id,
            "tables": tables,
        }
        return finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "tenant_authority_offboarding_manifest",
                **state,
                "authority_manifest_checksum": canonical_checksum(state),
                "prepared_at": _authority_now(self._clock).isoformat(),
            }
        )

    @staticmethod
    def _queue_cancellation_receipt(
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT receipt_id, receipt_json FROM tenant_queue_cancellations
            WHERE brand_id = ?
            """,
            (brand_id,),
        ).fetchone()
        if row is None:
            raise ContractError(
                "tenant work queue must be cancelled before authority offboarding"
            )
        try:
            receipt = _AuthorityWorkQueue._validated_cancellation_receipt(
                json.loads(row[1]), brand_id
            )
        except WorkQueueError as exc:
            raise PlatformAdapterError(
                "tenant queue cancellation evidence is invalid"
            ) from exc
        if receipt["queue_cancellation_receipt_id"] != row[0]:
            raise ContractError("tenant queue cancellation evidence is invalid")
        return receipt

    @staticmethod
    def _validated_artifact_deletion_receipt(
        receipt: Mapping[str, Any],
        brand_id: str,
        queue_cancellation_receipt_id: str,
    ) -> dict[str, Any]:
        validated = copy.deepcopy(dict(receipt))
        verify_record(validated)
        string_fields = {
            "deletion_receipt_id",
            "export_checksum",
            "queue_cancellation_receipt_id",
            "requested_by",
            "deleted_at",
        }
        if (
            validated.get("artifact_type")
            != "tenant_artifact_deletion_receipt"
            or validated.get("brand_id") != brand_id
            or any(
                not isinstance(validated.get(field), str)
                or not validated[field]
                for field in string_fields
            )
            or validated.get("queue_cancellation_receipt_id")
            != queue_cancellation_receipt_id
            or isinstance(validated.get("record_count"), bool)
            or not isinstance(validated.get("record_count"), int)
            or validated["record_count"] < 0
        ):
            raise PlatformAdapterError("artifact deletion evidence is invalid")
        deletion_seed = {
            key: validated[key]
            for key in (
                "brand_id",
                "export_checksum",
                "record_count",
                "queue_cancellation_receipt_id",
                "requested_by",
                "deleted_at",
            )
        }
        if validated["deletion_receipt_id"] != canonical_checksum(deletion_seed):
            raise PlatformAdapterError("artifact deletion evidence is invalid")
        parse_time(validated["deleted_at"])
        return validated

    @classmethod
    def _validated_receipt(
        cls,
        receipt: Mapping[str, Any],
        brand_id: str,
    ) -> dict[str, Any]:
        validated = copy.deepcopy(dict(receipt))
        verify_record(validated)
        required = {
            "tenant_offboarding_receipt_id",
            "authority_manifest_checksum",
            "artifact_deletion_receipt_id",
            "queue_cancellation_receipt_id",
            "manifest_table_row_counts",
            "evidence_ref",
            "requested_by",
            "offboarded_at",
        }
        if (
            validated.get("artifact_type")
            != "tenant_authority_offboarding_receipt"
            or validated.get("brand_id") != brand_id
            or not required.issubset(validated)
            or any(
                not isinstance(validated[field], str) or not validated[field]
                for field in required - {"manifest_table_row_counts"}
            )
            or not isinstance(validated["manifest_table_row_counts"], dict)
            or set(validated["manifest_table_row_counts"])
            != {table for table, _order_by in cls._TENANT_TABLES}
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                for count in validated["manifest_table_row_counts"].values()
            )
            or not validated["authority_manifest_checksum"].startswith("sha256:")
            or not validated["artifact_deletion_receipt_id"].startswith("sha256:")
            or not validated["queue_cancellation_receipt_id"].startswith("sha256:")
            or not validated["evidence_ref"].startswith("evidence://")
        ):
            raise PlatformAdapterError("tenant offboarding evidence is invalid")
        seed = {
            key: validated[key]
            for key in (
                "brand_id",
                "authority_manifest_checksum",
                "artifact_deletion_receipt_id",
                "queue_cancellation_receipt_id",
                "manifest_table_row_counts",
                "evidence_ref",
                "requested_by",
                "offboarded_at",
            )
        }
        if validated["tenant_offboarding_receipt_id"] != canonical_checksum(seed):
            raise PlatformAdapterError("tenant offboarding evidence is invalid")
        parse_time(validated["offboarded_at"])
        return validated

    @staticmethod
    def _require_director(principal: Principal) -> None:
        if principal.role_id != "agency-director":
            raise AuthorizationError(
                "only the agency director may offboard a tenant authority"
            )


class _AuthorityWorkQueue:
    """Durable tenant queue inside the protected fictional Platform Authority."""

    MAX_LEASE_SECONDS = 60

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime],
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_ADAPTER_TOKEN:
            raise WorkQueueError("work queue authority construction is denied")
        self._clock = clock
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=WorkQueueError,
        )

    def enqueue(self, principal: Principal, work_item: Mapping[str, Any]) -> str:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may enqueue work")
        item = self._validated_work_item(work_item)
        if item["brand_id"] != principal.brand_id:
            raise AuthorizationError("cross-tenant work enqueue denied")
        if item["created_by"] != principal.actor_id:
            raise AuthorizationError("work queue creator does not match actor")
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            existing = connection.execute(
                """
                SELECT work_checksum FROM platform_work_queue
                WHERE brand_id = ? AND work_item_id = ?
                """,
                (principal.brand_id, item["work_item_id"]),
            ).fetchone()
            if existing is not None:
                if existing["work_checksum"] != item["content_checksum"]:
                    raise ContractError("work queue item is immutable")
                connection.commit()
                return item["work_item_id"]
            self._require_current_task(connection, item)
            timestamp = now.isoformat()
            connection.execute(
                """
                INSERT INTO platform_work_queue (
                    brand_id,
                    work_item_id,
                    work_json,
                    work_checksum,
                    work_kind,
                    worker_role,
                    state,
                    attempt_count,
                    max_attempts,
                    next_attempt_at,
                    leased_at,
                    lease_owner,
                    lease_token_hash,
                    lease_expires_at,
                    heartbeat_at,
                    error_classes_json,
                    disposition_json,
                    completed_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'READY', 0, ?, ?, NULL, NULL,
                          NULL, NULL, NULL, '[]', NULL, NULL, ?, ?)
                """,
                (
                    principal.brand_id,
                    item["work_item_id"],
                    canonical_bytes(item).decode("utf-8"),
                    item["content_checksum"],
                    item["work_kind"],
                    item["worker_role"],
                    item["max_attempts"],
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "queue.item.enqueued",
                item["work_item_id"],
            )
            connection.commit()
            return item["work_item_id"]
        except (AuthorizationError, ContractError, WorkQueueError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not enqueue work") from exc
        finally:
            connection.close()

    def lease_next(
        self, principal: Principal, lease_seconds: int
    ) -> dict[str, Any] | None:
        self._validate_lease_seconds(lease_seconds)
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            self._expire_leases(connection, principal, now)
            while True:
                row = connection.execute(
                    """
                    SELECT * FROM platform_work_queue
                    WHERE brand_id = ?
                      AND worker_role = ?
                      AND (
                        state = 'READY'
                        OR (
                          state = 'RETRY_WAIT'
                          AND next_attempt_at IS NOT NULL
                          AND next_attempt_at <= ?
                        )
                      )
                    ORDER BY created_at, work_item_id
                    LIMIT 1
                    """,
                    (principal.brand_id, principal.role_id, now.isoformat()),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                work_item = self._validated_work_item(json.loads(row["work_json"]))
                if not self._task_is_current(connection, work_item):
                    self._move_to_dead_letter(
                        connection,
                        principal,
                        row,
                        now,
                        "TASK_DRIFT",
                    )
                    continue
                token = secrets.token_urlsafe(32)
                expires_at = now + timedelta(seconds=lease_seconds)
                attempt_count = int(row["attempt_count"]) + 1
                connection.execute(
                    """
                    UPDATE platform_work_queue
                    SET state = 'LEASED',
                        attempt_count = ?,
                        next_attempt_at = NULL,
                        leased_at = ?,
                        lease_owner = ?,
                        lease_token_hash = ?,
                        lease_expires_at = ?,
                        heartbeat_at = ?,
                        updated_at = ?
                    WHERE brand_id = ? AND work_item_id = ?
                    """,
                    (
                        attempt_count,
                        now.isoformat(),
                        principal.actor_id,
                        self._token_hash(token),
                        expires_at.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        principal.brand_id,
                        row["work_item_id"],
                    ),
                )
                _AuthorityPaperclipAdapter._insert_audit(
                    connection,
                    principal,
                    "queue.item.leased",
                    row["work_item_id"],
                )
                connection.commit()
                return {
                    "work_item": work_item,
                    "lease": {
                        "work_item_id": row["work_item_id"],
                        "lease_token": token,
                        "lease_owner": principal.actor_id,
                        "leased_at": now.isoformat(),
                        "lease_expires_at": expires_at.isoformat(),
                        "attempt_count": attempt_count,
                    },
                }
        except (AuthorizationError, ContractError, WorkQueueError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not lease queued work") from exc
        finally:
            connection.close()

    def heartbeat(
        self,
        principal: Principal,
        work_item_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        self._validate_lease_seconds(lease_seconds)
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            row = self._required_row(connection, principal.brand_id, work_item_id)
            self._require_active_lease(row, principal, lease_token, now)
            expires_at = now + timedelta(seconds=lease_seconds)
            connection.execute(
                """
                UPDATE platform_work_queue
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE brand_id = ? AND work_item_id = ?
                """,
                (
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    principal.brand_id,
                    work_item_id,
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection, principal, "queue.item.heartbeat", work_item_id
            )
            connection.commit()
            return {
                "work_item_id": work_item_id,
                "lease_owner": principal.actor_id,
                "lease_expires_at": expires_at.isoformat(),
                "attempt_count": int(row["attempt_count"]),
            }
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            _rollback(connection)
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not renew work lease") from exc
        finally:
            connection.close()

    def complete(
        self,
        principal: Principal,
        work_item_id: str,
        lease_token: str,
    ) -> dict[str, Any]:
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            row = self._required_row(connection, principal.brand_id, work_item_id)
            self._require_active_lease(row, principal, lease_token, now)
            work_item = self._validated_work_item(json.loads(row["work_json"]))
            if not self._task_is_current(connection, work_item):
                if row["work_kind"] == "internal":
                    self._move_to_dead_letter(
                        connection,
                        principal,
                        row,
                        now,
                        "TASK_DRIFT",
                    )
                else:
                    errors = self._error_classes(row)
                    errors.append("TASK_DRIFT")
                    self._set_nonleased_state(
                        connection,
                        row,
                        state="RECONCILIATION_REQUIRED",
                        now=now,
                        errors=errors,
                        next_attempt_at=None,
                    )
                    _AuthorityPaperclipAdapter._insert_audit(
                        connection,
                        principal,
                        "queue.item.task_drift.reconciliation_required",
                        work_item_id,
                    )
                result = self._view(
                    self._required_row(connection, principal.brand_id, work_item_id)
                )
                connection.commit()
                return result
            connection.execute(
                """
                UPDATE platform_work_queue
                SET state = 'COMPLETED',
                    leased_at = NULL,
                    lease_owner = NULL,
                    lease_token_hash = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE brand_id = ? AND work_item_id = ?
                """,
                (
                    now.isoformat(),
                    now.isoformat(),
                    principal.brand_id,
                    work_item_id,
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection, principal, "queue.item.completed", work_item_id
            )
            row = self._required_row(connection, principal.brand_id, work_item_id)
            result = self._view(row)
            connection.commit()
            return result
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not complete queued work") from exc
        finally:
            connection.close()

    def fail(
        self,
        principal: Principal,
        work_item_id: str,
        lease_token: str,
        *,
        error_class: str,
        retryable: bool,
        external_result: str,
    ) -> dict[str, Any]:
        if error_class not in WORK_ERROR_CLASSES - {
            "LEASE_EXPIRED",
            "TASK_DRIFT",
            "TENANT_OFFBOARDED",
        }:
            raise ContractError("work queue error class is invalid")
        if not isinstance(retryable, bool):
            raise ContractError("work queue retryable flag is invalid")
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            row = self._required_row(connection, principal.brand_id, work_item_id)
            self._require_active_lease(row, principal, lease_token, now)
            if row["work_kind"] == "internal":
                if external_result != "NOT_APPLICABLE":
                    raise ContractError("internal work cannot report external state")
            elif external_result not in {
                "UNKNOWN",
                "CONFIRMED_NO_WRITE",
                "CONFIRMED_REJECTED",
            }:
                raise ContractError("external work result state is invalid")
            errors = self._error_classes(row)
            errors.append(error_class)
            if row["work_kind"] == "external_write" and external_result == "UNKNOWN":
                state = "RECONCILIATION_REQUIRED"
                next_attempt_at = None
            elif retryable and int(row["attempt_count"]) < int(row["max_attempts"]):
                state = "RETRY_WAIT"
                next_attempt_at = self._retry_time(
                    now, int(row["attempt_count"])
                ).isoformat()
            else:
                state = "DEAD_LETTER"
                next_attempt_at = None
            self._set_nonleased_state(
                connection,
                row,
                state=state,
                now=now,
                errors=errors,
                next_attempt_at=next_attempt_at,
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                f"queue.item.{state.lower()}",
                work_item_id,
            )
            result = self._view(
                self._required_row(connection, principal.brand_id, work_item_id)
            )
            connection.commit()
            return result
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not record work failure") from exc
        finally:
            connection.close()

    def reconcile(
        self,
        principal: Principal,
        work_item_id: str,
        *,
        outcome: str,
        evidence_ref: str,
        disposition: str,
    ) -> dict[str, Any]:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may reconcile work")
        if outcome not in WORK_RECONCILIATION_OUTCOMES:
            raise ContractError("work reconciliation outcome is invalid")
        if not all(isinstance(value, str) and value for value in (evidence_ref, disposition)):
            raise ContractError("work reconciliation evidence and disposition are required")
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            row = self._required_row(connection, principal.brand_id, work_item_id)
            if row["state"] != "RECONCILIATION_REQUIRED":
                raise ContractError("work item does not require reconciliation")
            disposition_record = {
                "outcome": outcome,
                "evidence_ref": evidence_ref,
                "disposition": disposition,
                "decided_by": principal.actor_id,
                "decided_at": now.isoformat(),
            }
            dispositions = self._dispositions(row)
            dispositions.append(disposition_record)
            if outcome == "CONFIRMED_COMPLETED":
                state = "COMPLETED"
                completed_at = now.isoformat()
                next_attempt_at = None
            elif outcome == "CONFIRMED_NO_WRITE" and int(row["attempt_count"]) < int(
                row["max_attempts"]
            ):
                state = "READY"
                completed_at = None
                next_attempt_at = now.isoformat()
            else:
                state = "DEAD_LETTER"
                completed_at = None
                next_attempt_at = None
            connection.execute(
                """
                UPDATE platform_work_queue
                SET state = ?,
                    next_attempt_at = ?,
                    disposition_json = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE brand_id = ? AND work_item_id = ?
                """,
                (
                    state,
                    next_attempt_at,
                    canonical_bytes(dispositions).decode("utf-8"),
                    completed_at,
                    now.isoformat(),
                    principal.brand_id,
                    work_item_id,
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                f"queue.item.reconciled.{state.lower()}",
                work_item_id,
            )
            result = self._view(
                self._required_row(connection, principal.brand_id, work_item_id)
            )
            connection.commit()
            return result
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not reconcile queued work") from exc
        finally:
            connection.close()

    def record_dead_letter_disposition(
        self,
        principal: Principal,
        work_item_id: str,
        *,
        evidence_ref: str,
        disposition: str,
    ) -> dict[str, Any]:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may disposition dead letters")
        if not all(isinstance(value, str) and value for value in (evidence_ref, disposition)):
            raise ContractError("dead-letter evidence and disposition are required")
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant_active(connection, principal.brand_id)
            row = self._required_row(connection, principal.brand_id, work_item_id)
            if row["state"] != "DEAD_LETTER":
                raise ContractError("work item is not dead-lettered")
            disposition_record = {
                "outcome": "DEAD_LETTER",
                "evidence_ref": evidence_ref,
                "disposition": disposition,
                "decided_by": principal.actor_id,
                "decided_at": now.isoformat(),
            }
            dispositions = self._dispositions(row)
            dead_letter_dispositions = [
                existing
                for existing in dispositions
                if existing.get("outcome") == "DEAD_LETTER"
            ]
            if dead_letter_dispositions:
                existing = dead_letter_dispositions[0]
                if any(
                    existing.get(field) != disposition_record[field]
                    for field in ("evidence_ref", "disposition", "decided_by")
                ):
                    raise ContractError("dead-letter disposition is immutable")
                connection.commit()
                return self._view(row)
            dispositions.append(disposition_record)
            encoded = canonical_bytes(dispositions).decode("utf-8")
            connection.execute(
                """
                UPDATE platform_work_queue
                SET disposition_json = ?, updated_at = ?
                WHERE brand_id = ? AND work_item_id = ?
                """,
                (encoded, now.isoformat(), principal.brand_id, work_item_id),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "queue.item.dead_letter_dispositioned",
                work_item_id,
            )
            result = self._view(
                self._required_row(connection, principal.brand_id, work_item_id)
            )
            connection.commit()
            return result
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not disposition dead-letter work") from exc
        finally:
            connection.close()

    def cancel_tenant(
        self,
        principal: Principal,
        *,
        evidence_ref: str,
    ) -> dict[str, Any]:
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may cancel tenant work")
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.startswith("evidence://")
            or len(evidence_ref) > 512
        ):
            raise ContractError("tenant queue cancellation evidence is required")
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT receipt_json FROM tenant_queue_cancellations
                WHERE brand_id = ?
                """,
                (principal.brand_id,),
            ).fetchone()
            if existing is not None:
                receipt = self._validated_cancellation_receipt(
                    json.loads(existing["receipt_json"]), principal.brand_id
                )
                if receipt["evidence_ref"] != evidence_ref:
                    raise ContractError("tenant queue cancellation is immutable")
                connection.commit()
                return receipt

            rows = connection.execute(
                """
                SELECT * FROM platform_work_queue
                WHERE brand_id = ?
                ORDER BY work_item_id
                """,
                (principal.brand_id,),
            ).fetchall()
            if any(
                row["work_kind"] == "external_write"
                and row["state"] in {"LEASED", "RECONCILIATION_REQUIRED"}
                for row in rows
            ):
                raise ContractError(
                    "uncertain external work must be reconciled before offboarding"
                )
            cancelled_count = 0
            for row in rows:
                if row["state"] in {
                    "READY",
                    "LEASED",
                    "RETRY_WAIT",
                    "RECONCILIATION_REQUIRED",
                }:
                    errors = self._error_classes(row)
                    errors.append("TENANT_OFFBOARDED")
                    self._set_nonleased_state(
                        connection,
                        row,
                        state="DEAD_LETTER",
                        now=now,
                        errors=errors,
                        next_attempt_at=None,
                    )
                    _AuthorityPaperclipAdapter._insert_audit(
                        connection,
                        principal,
                        "queue.item.offboarding_dead_letter",
                        row["work_item_id"],
                    )
                    cancelled_count += 1
            receipt_seed = {
                "brand_id": principal.brand_id,
                "evidence_ref": evidence_ref,
                "work_item_count": len(rows),
                "cancelled_item_count": cancelled_count,
                "terminal_item_count": len(rows) - cancelled_count,
                "requested_by": principal.actor_id,
                "cancelled_at": now.isoformat(),
            }
            receipt_id = canonical_checksum(receipt_seed)
            receipt = finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "tenant_queue_cancellation_receipt",
                    "queue_cancellation_receipt_id": receipt_id,
                    **receipt_seed,
                }
            )
            connection.execute(
                """
                INSERT INTO tenant_queue_cancellations (
                    brand_id, receipt_id, evidence_ref, receipt_json, cancelled_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    principal.brand_id,
                    receipt_id,
                    evidence_ref,
                    canonical_bytes(receipt).decode("utf-8"),
                    now.isoformat(),
                ),
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                "queue.tenant.cancelled",
                receipt_id,
            )
            connection.commit()
            return receipt
        except (AuthorizationError, ContractError, WorkQueueError):
            _rollback(connection)
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            _rollback(connection)
            raise WorkQueueError("could not cancel tenant work") from exc
        finally:
            connection.close()

    def cancellation_receipt(
        self,
        principal: Principal,
        receipt_id: str,
    ) -> dict[str, Any]:
        if principal.role_id not in {
            "agency-director",
            "platform-assurance-reviewer",
        }:
            raise AuthorizationError("role cannot read queue cancellation evidence")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT receipt_json FROM tenant_queue_cancellations
                WHERE brand_id = ? AND receipt_id = ?
                """,
                (principal.brand_id, receipt_id),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            return self._validated_cancellation_receipt(
                json.loads(row["receipt_json"]), principal.brand_id
            )
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise WorkQueueError("could not read queue cancellation evidence") from exc
        finally:
            connection.close()

    def get(self, principal: Principal, work_item_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = self._required_row(connection, principal.brand_id, work_item_id)
            tenant_cancelled = connection.execute(
                """
                SELECT 1 FROM tenant_queue_cancellations
                WHERE brand_id = ?
                """,
                (principal.brand_id,),
            ).fetchone()
            if tenant_cancelled is not None and principal.role_id not in {
                "agency-director",
                "platform-assurance-reviewer",
            }:
                raise AuthorizationError("worker queue access is closed after offboarding")
            if principal.role_id not in {
                "agency-director",
                "platform-assurance-reviewer",
                row["worker_role"],
            }:
                raise AuthorizationError("role cannot read this work item")
            return self._view(row)
        except (AuthorizationError, ContractError, WorkQueueError, KeyError):
            raise
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise WorkQueueError("could not read queued work") from exc
        finally:
            connection.close()

    def dead_letters(self, principal: Principal) -> list[dict[str, Any]]:
        if principal.role_id not in {
            "agency-director",
            "platform-assurance-reviewer",
        }:
            raise AuthorizationError("role cannot read dead-letter work")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM platform_work_queue
                WHERE brand_id = ? AND state = 'DEAD_LETTER'
                ORDER BY updated_at, work_item_id
                """,
                (principal.brand_id,),
            ).fetchall()
            return [self._view(row) for row in rows]
        except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise WorkQueueError("could not list dead-letter work") from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = self._database.connect()
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _require_tenant_active(
        connection: sqlite3.Connection,
        brand_id: str,
    ) -> None:
        cancelled = connection.execute(
            """
            SELECT 1 FROM tenant_queue_cancellations
            WHERE brand_id = ?
            """,
            (brand_id,),
        ).fetchone()
        if cancelled is not None:
            raise ContractError("tenant work queue is closed after offboarding")

    @staticmethod
    def _validated_cancellation_receipt(
        raw_receipt: Mapping[str, Any],
        brand_id: str,
    ) -> dict[str, Any]:
        receipt = copy.deepcopy(dict(raw_receipt))
        verify_record(receipt)
        required = {
            "queue_cancellation_receipt_id",
            "brand_id",
            "evidence_ref",
            "work_item_count",
            "cancelled_item_count",
            "terminal_item_count",
            "requested_by",
            "cancelled_at",
        }
        if (
            receipt.get("artifact_type")
            != "tenant_queue_cancellation_receipt"
            or receipt.get("brand_id") != brand_id
            or not required.issubset(receipt)
        ):
            raise WorkQueueError("stored queue cancellation receipt is invalid")
        if not all(
            isinstance(receipt[field], str) and receipt[field]
            for field in (
                "queue_cancellation_receipt_id",
                "evidence_ref",
                "requested_by",
                "cancelled_at",
            )
        ):
            raise WorkQueueError("stored queue cancellation receipt is invalid")
        if (
            not receipt["evidence_ref"].startswith("evidence://")
            or len(receipt["evidence_ref"]) > 512
        ):
            raise WorkQueueError("stored queue cancellation receipt is invalid")
        counts = [
            receipt["work_item_count"],
            receipt["cancelled_item_count"],
            receipt["terminal_item_count"],
        ]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
            raise WorkQueueError("stored queue cancellation receipt is invalid")
        if any(value < 0 for value in counts) or counts[1] + counts[2] != counts[0]:
            raise WorkQueueError("stored queue cancellation receipt is invalid")
        receipt_seed = {
            key: receipt[key]
            for key in (
                "brand_id",
                "evidence_ref",
                "work_item_count",
                "cancelled_item_count",
                "terminal_item_count",
                "requested_by",
                "cancelled_at",
            )
        }
        if receipt["queue_cancellation_receipt_id"] != canonical_checksum(receipt_seed):
            raise WorkQueueError("stored queue cancellation receipt is invalid")
        parse_time(receipt["cancelled_at"])
        return receipt

    def _expire_leases(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        now: datetime,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM platform_work_queue
            WHERE brand_id = ?
              AND state = 'LEASED'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            ORDER BY work_item_id
            """,
            (principal.brand_id, now.isoformat()),
        ).fetchall()
        for row in rows:
            errors = self._error_classes(row)
            errors.append("LEASE_EXPIRED")
            if row["work_kind"] == "external_write":
                state = "RECONCILIATION_REQUIRED"
                next_attempt_at = None
            elif int(row["attempt_count"]) < int(row["max_attempts"]):
                state = "RETRY_WAIT"
                next_attempt_at = now.isoformat()
            else:
                state = "DEAD_LETTER"
                next_attempt_at = None
            self._set_nonleased_state(
                connection,
                row,
                state=state,
                now=now,
                errors=errors,
                next_attempt_at=next_attempt_at,
            )
            _AuthorityPaperclipAdapter._insert_audit(
                connection,
                principal,
                f"queue.item.lease_expired.{state.lower()}",
                row["work_item_id"],
            )

    def _move_to_dead_letter(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        row: sqlite3.Row,
        now: datetime,
        error_class: str,
    ) -> None:
        errors = self._error_classes(row)
        errors.append(error_class)
        self._set_nonleased_state(
            connection,
            row,
            state="DEAD_LETTER",
            now=now,
            errors=errors,
            next_attempt_at=None,
        )
        _AuthorityPaperclipAdapter._insert_audit(
            connection,
            principal,
            "queue.item.dead_letter",
            row["work_item_id"],
        )

    @staticmethod
    def _set_nonleased_state(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        state: str,
        now: datetime,
        errors: Sequence[str],
        next_attempt_at: str | None,
    ) -> None:
        if state not in WORK_QUEUE_STATES - {"READY", "LEASED", "COMPLETED"}:
            raise WorkQueueError("invalid nonleased queue state")
        connection.execute(
            """
            UPDATE platform_work_queue
            SET state = ?,
                next_attempt_at = ?,
                leased_at = NULL,
                lease_owner = NULL,
                lease_token_hash = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                error_classes_json = ?,
                updated_at = ?
            WHERE brand_id = ? AND work_item_id = ?
            """,
            (
                state,
                next_attempt_at,
                canonical_bytes(list(errors)).decode("utf-8"),
                now.isoformat(),
                row["brand_id"],
                row["work_item_id"],
            ),
        )

    @staticmethod
    def _required_row(
        connection: sqlite3.Connection,
        brand_id: str,
        work_item_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM platform_work_queue
            WHERE brand_id = ? AND work_item_id = ?
            """,
            (brand_id, work_item_id),
        ).fetchone()
        if row is None:
            raise KeyError(work_item_id)
        return row

    def _require_active_lease(
        self,
        row: sqlite3.Row,
        principal: Principal,
        lease_token: str,
        now: datetime,
    ) -> None:
        if row["worker_role"] != principal.role_id:
            raise AuthorizationError("work lease role does not match actor")
        if row["state"] != "LEASED" or row["lease_owner"] != principal.actor_id:
            raise AuthorizationError("work lease is not held by this actor")
        if not isinstance(lease_token, str) or not lease_token:
            raise AuthorizationError("work lease token is invalid")
        if row["lease_token_hash"] != self._token_hash(lease_token):
            raise AuthorizationError("work lease token is invalid")
        expires_at = row["lease_expires_at"]
        if not isinstance(expires_at, str) or parse_time(expires_at) <= now:
            raise ContractError("work lease has expired")

    def _require_current_task(
        self, connection: sqlite3.Connection, item: Mapping[str, Any]
    ) -> None:
        if not self._task_is_current(connection, item):
            raise ContractError("work item is not bound to the current ready task")

    @staticmethod
    def _task_is_current(
        connection: sqlite3.Connection, item: Mapping[str, Any]
    ) -> bool:
        row = connection.execute(
            """
            SELECT record_json, checksum FROM paperclip_task_versions
            WHERE brand_id = ? AND issue_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (item["brand_id"], item["paperclip_issue_id"]),
        ).fetchone()
        if row is None:
            return False
        task = json.loads(row["record_json"])
        _validate_task(task)
        return (
            row["checksum"] == item["paperclip_task_checksum"]
            and task["content_checksum"] == item["paperclip_task_checksum"]
            and task["status"] in {"ready", "in_progress"}
        )

    @staticmethod
    def _validated_work_item(work_item: Mapping[str, Any]) -> dict[str, Any]:
        verify_record(work_item)
        item = copy.deepcopy(dict(work_item))
        required = {
            "work_item_id",
            "brand_id",
            "paperclip_issue_id",
            "paperclip_task_checksum",
            "work_kind",
            "worker_role",
            "payload",
            "max_attempts",
            "created_by",
            "created_at",
        }
        if item.get("artifact_type") != "work_queue_item" or not required.issubset(item):
            raise ContractError("invalid work queue item")
        if not all(
            isinstance(item[field], str) and item[field]
            for field in (
                "work_item_id",
                "brand_id",
                "paperclip_issue_id",
                "paperclip_task_checksum",
                "worker_role",
                "created_by",
                "created_at",
            )
        ):
            raise ContractError("invalid work queue identity")
        if not item["brand_id"].startswith("brand_"):
            raise ContractError("invalid work queue brand")
        if item["work_kind"] not in WORK_KINDS:
            raise ContractError("invalid work queue kind")
        if item["worker_role"] not in WORKER_QUEUE_ROLES:
            raise ContractError("invalid work queue worker role")
        if not isinstance(item["payload"], dict):
            raise ContractError("invalid work queue payload")
        max_attempts = item["max_attempts"]
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ContractError("invalid work queue attempt policy")
        if not 1 <= max_attempts <= 5:
            raise ContractError("invalid work queue attempt policy")
        parse_time(item["created_at"])
        return item

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_lease_seconds(lease_seconds: int) -> None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise ContractError("work lease duration must be an integer")
        if not 1 <= lease_seconds <= _AuthorityWorkQueue.MAX_LEASE_SECONDS:
            raise ContractError("work lease duration must be between 1 and 60 seconds")

    @staticmethod
    def _retry_time(now: datetime, attempt_count: int) -> datetime:
        return now + timedelta(seconds=min(60, 2**attempt_count))

    @staticmethod
    def _error_classes(row: sqlite3.Row) -> list[str]:
        errors = json.loads(row["error_classes_json"])
        if (
            not isinstance(errors, list)
            or any(error not in WORK_ERROR_CLASSES for error in errors)
        ):
            raise WorkQueueError("stored work error classes are invalid")
        return list(errors)

    @staticmethod
    def _dispositions(row: sqlite3.Row) -> list[dict[str, Any]]:
        if row["disposition_json"] is None:
            return []
        dispositions = json.loads(row["disposition_json"])
        if not isinstance(dispositions, list) or any(
            not isinstance(disposition, dict) for disposition in dispositions
        ):
            raise WorkQueueError("stored work dispositions are invalid")
        return copy.deepcopy(dispositions)

    def _view(self, row: sqlite3.Row) -> dict[str, Any]:
        state = row["state"]
        if state not in WORK_QUEUE_STATES:
            raise WorkQueueError("stored work queue state is invalid")
        item = self._validated_work_item(json.loads(row["work_json"]))
        if (
            item["brand_id"] != row["brand_id"]
            or item["work_item_id"] != row["work_item_id"]
            or item["content_checksum"] != row["work_checksum"]
            or item["work_kind"] != row["work_kind"]
            or item["worker_role"] != row["worker_role"]
            or item["max_attempts"] != row["max_attempts"]
        ):
            raise WorkQueueError("stored work queue identity is invalid")
        return {
            "work_item": item,
            "state": state,
            "attempt_count": int(row["attempt_count"]),
            "max_attempts": int(row["max_attempts"]),
            "next_attempt_at": row["next_attempt_at"],
            "lease_owner": row["lease_owner"],
            "leased_at": row["leased_at"],
            "lease_expires_at": row["lease_expires_at"],
            "heartbeat_at": row["heartbeat_at"],
            "error_classes": self._error_classes(row),
            "dispositions": self._dispositions(row),
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _authority_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("authority clock must return a timezone-aware value")
    return now


def _validate_approver_policy(policy: Mapping[str, Any]) -> None:
    verify_record(policy)
    required = {
        "policy_id",
        "brand_id",
        "revision",
        "previous_policy_checksum",
        "permitted_approver_ids",
        "issued_by",
        "effective_at",
    }
    if policy.get("artifact_type") != "approver_policy" or not required.issubset(
        policy
    ):
        raise ContractError("invalid approver policy")
    if not isinstance(policy["brand_id"], str) or not policy["brand_id"].startswith(
        "brand_"
    ):
        raise ContractError("invalid approver policy brand")
    if (
        not isinstance(policy["policy_id"], str)
        or not policy["policy_id"]
        or not isinstance(policy["issued_by"], str)
        or not policy["issued_by"]
    ):
        raise ContractError("invalid approver policy identity")
    revision = policy["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractError("invalid approver policy revision")
    permitted = policy["permitted_approver_ids"]
    if (
        not isinstance(permitted, list)
        or not permitted
        or any(not isinstance(actor_id, str) or not actor_id for actor_id in permitted)
        or len(set(permitted)) != len(permitted)
    ):
        raise ContractError("invalid approver policy actors")
    if revision == 1 and policy["previous_policy_checksum"] is not None:
        raise ContractError("invalid first approver policy predecessor")
    if revision > 1 and (
        not isinstance(policy["previous_policy_checksum"], str)
        or not policy["previous_policy_checksum"]
    ):
        raise ContractError("invalid approver policy predecessor")
    parse_time(policy["effective_at"])


def _validate_task(task: Mapping[str, Any]) -> None:
    verify_record(task)
    required = {
        "paperclip_issue_id",
        "brand_id",
        "campaign_id",
        "task_type",
        "title",
        "version",
        "previous_checksum",
        "status",
        "dependencies",
        "acceptance_criteria",
        "approval_required",
        "budget",
        "completion_evidence_refs",
        "created_by",
        "created_at",
        "updated_at",
    }
    if task.get("artifact_type") != "paperclip_task" or not required.issubset(task):
        raise ContractError("invalid Paperclip task record")
    if task["status"] not in TASK_STATUSES:
        raise ContractError("invalid Paperclip task status")
    if not isinstance(task["brand_id"], str) or not task["brand_id"].startswith(
        "brand_"
    ):
        raise ContractError("invalid Paperclip task brand")
    if not isinstance(task["version"], int) or task["version"] < 1:
        raise ContractError("invalid Paperclip task version")
    if not isinstance(task["approval_required"], bool):
        raise ContractError("invalid Paperclip approval requirement")
    if not isinstance(task["dependencies"], list) or not isinstance(
        task["acceptance_criteria"], list
    ):
        raise ContractError("invalid Paperclip task arrays")
    if (
        len(set(task["dependencies"])) != len(task["dependencies"])
        or task["paperclip_issue_id"] in task["dependencies"]
    ):
        raise ContractError("invalid Paperclip task dependencies")
    if not task["acceptance_criteria"]:
        raise ContractError("Paperclip task has no acceptance criteria")
    budget = task["budget"]
    if (
        not isinstance(budget, Mapping)
        or not isinstance(budget.get("limit_minor"), int)
        or not isinstance(budget.get("spent_minor"), int)
        or budget["limit_minor"] < 0
        or budget["spent_minor"] < 0
        or budget["spent_minor"] > budget["limit_minor"]
    ):
        raise ContractError("invalid Paperclip task budget")
    parse_time(task["created_at"])
    parse_time(task["updated_at"])
    if task["status"] == "done" and not task["completion_evidence_refs"]:
        raise ContractError("done Paperclip task has no completion evidence")
    if task["status"] != "done" and task["completion_evidence_refs"]:
        raise ContractError("unfinished Paperclip task has completion evidence")


def _validate_buzz_context(packet: Mapping[str, Any]) -> None:
    verify_record(packet)
    required = {
        "context_id",
        "brand_id",
        "campaign_id",
        "paperclip_issue_id",
        "purpose",
        "decision_needed",
        "participants",
        "source_artifact_ids",
        "constraints",
        "deadline",
        "exit_condition",
        "created_by",
        "created_at",
    }
    if packet.get("artifact_type") != "buzz_context_packet" or not required.issubset(
        packet
    ):
        raise ContractError("invalid Buzz context packet")
    participants = packet["participants"]
    if (
        not isinstance(participants, list)
        or not participants
        or any(not isinstance(actor_id, str) or not actor_id for actor_id in participants)
        or len(set(participants)) != len(participants)
    ):
        raise ContractError("Buzz context has invalid participants")
    if not isinstance(packet["source_artifact_ids"], list) or not isinstance(
        packet["constraints"], list
    ):
        raise ContractError("Buzz context evidence and constraints must be arrays")
    parse_time(packet["deadline"])
    parse_time(packet["created_at"])


def _validate_buzz_decision(decision: Mapping[str, Any]) -> None:
    verify_record(decision)
    required = {
        "decision_id",
        "brand_id",
        "campaign_id",
        "paperclip_issue_id",
        "context_id",
        "context_checksum",
        "summary",
        "source_event_ids",
        "recorded_by",
        "recorded_at",
    }
    if decision.get("artifact_type") != "buzz_decision_summary" or not required.issubset(
        decision
    ):
        raise ContractError("invalid Buzz decision summary")
    source_event_ids = decision["source_event_ids"]
    if (
        not decision["summary"]
        or not isinstance(source_event_ids, list)
        or not source_event_ids
        or any(not isinstance(event_id, str) or not event_id for event_id in source_event_ids)
        or len(set(source_event_ids)) != len(source_event_ids)
    ):
        raise ContractError("Buzz decision has invalid summary evidence")
    parse_time(decision["recorded_at"])


def _validate_evidence(evidence: Mapping[str, Any]) -> None:
    verify_record(evidence)
    required = {
        "evidence_id",
        "brand_id",
        "paperclip_issue_id",
        "source_ref",
        "source_class",
        "retrieved_at",
        "claim",
        "extract",
        "confidence",
        "created_by",
    }
    if evidence.get("artifact_type") != "evidence_record" or not required.issubset(
        evidence
    ):
        raise ContractError("invalid evidence record")
    if evidence["source_class"] not in {"primary", "first_party", "internal_test"}:
        raise ContractError("evidence source class is invalid")
    confidence = evidence["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ContractError("evidence confidence is invalid")
    if not 0 <= confidence <= 1:
        raise ContractError("evidence confidence is invalid")
    parse_time(evidence["retrieved_at"])


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass
