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
                CREATE TABLE IF NOT EXISTS platform_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    brand_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
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
    ) -> tuple[str] | None:
        return connection.execute(
            """
            SELECT receipt_json FROM tenant_artifact_deletions
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
            deleted_at = _authority_now(self._clock).isoformat()
            receipt_seed = {
                "brand_id": principal.brand_id,
                "export_checksum": expected_export_checksum,
                "record_count": exported["record_count"],
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
            receipt = json.loads(row[0])
            verify_record(receipt)
            if (
                receipt.get("artifact_type")
                != "tenant_artifact_deletion_receipt"
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
        if error_class not in WORK_ERROR_CLASSES - {"LEASE_EXPIRED", "TASK_DRIFT"}:
            raise ContractError("work queue error class is invalid")
        if not isinstance(retryable, bool):
            raise ContractError("work queue retryable flag is invalid")
        now = _authority_now(self._clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
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

    def get(self, principal: Principal, work_item_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = self._required_row(connection, principal.brand_id, work_item_id)
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
