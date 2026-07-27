"""Typed fictional Paperclip/Buzz adapters and persistent tenant evidence.

This module is a local Gate 5 reference boundary.  It deliberately performs no
network calls and does not claim compatibility with an installed Paperclip or
Buzz service.  Paperclip-shaped state is authoritative; Buzz-shaped context can
only append a decision summary and cannot mutate task state, budget, or
approval.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    ContractError,
    canonical_bytes,
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
from .store import AuthorizationError, Principal


class PlatformAdapterError(RuntimeError):
    """A typed platform request could not be accepted or persisted."""


class EvidenceStoreError(RuntimeError):
    """The persistent tenant evidence authority failed closed."""


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


class FictionalPaperclipAdapter:
    """Durable, typed local stand-in for Paperclip's authoritative state."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = _SQLitePlatformDatabase(
            database_path,
            timeout_seconds=timeout_seconds,
            error_type=PlatformAdapterError,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

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
            approval = finalize_record(
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
            dependency = FictionalPaperclipAdapter._read_current_task(
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
        verify_record(approval)
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
        paperclip: FictionalPaperclipAdapter,
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


class SQLiteTenantEvidenceStore:
    """Persistent immutable evidence partitioned by brand and Paperclip issue."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
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
            FictionalPaperclipAdapter._insert_audit(
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
