"""Governed, tenant-scoped Fleet Brand Agent and reversible follow-up action.

The Brand Agent never treats model output, user input, transcripts, or external
content as brand truth. It reads only active, approved Brand Twin records and
returns exact citations. Paperclip remains authoritative for follow-up work.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .brand_intelligence import BrandIntelligenceAuthority
from .contracts import (
    ContractError,
    canonical_bytes,
    finalize_record,
    parse_time,
    utc_now,
    verify_record,
)
from .fleet_tenancy import FleetTenantAuthority
from .integrations import PaperclipLifecycleAdapter
from .sqlite_storage import (
    SQLiteStorageError,
    prepare_sqlite_storage,
    validate_sqlite_storage,
)
from .store import Principal


class BrandAgentError(RuntimeError):
    """The Brand Agent could not complete a governed operation."""


class BrandAgentAuthorizationError(PermissionError):
    """The caller or requested operation is outside the admitted boundary."""


class BrandAgentActionUnknown(BrandAgentError):
    """The action outcome is uncertain and requires human reconciliation."""


class FollowUpTaskAdapter(Protocol):
    """Narrow Paperclip surface admitted for the reversible action."""

    def create(
        self, manifest: Mapping[str, Any], idempotency_key: str
    ) -> Mapping[str, Any]: ...

    def cancel(self, issue_id: str, receipt_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class BrandAgentPolicy:
    """Immutable release policy for one Brand Agent deployment."""

    brand_id: str
    public_claim_ids: tuple[str, ...]
    max_question_chars: int = 1200
    max_answer_claims: int = 3
    transcript_retention_days: int = 30
    composer_version: str = "evidence-bound-deterministic-v1"

    def __post_init__(self) -> None:
        if not self.brand_id.startswith("brand_"):
            raise ValueError("Brand Agent policy brand is invalid")
        if not self.public_claim_ids or len(set(self.public_claim_ids)) != len(
            self.public_claim_ids
        ):
            raise ValueError("Brand Agent public claim allowlist is invalid")
        if not 1 <= self.max_question_chars <= 4000:
            raise ValueError("Brand Agent question limit is invalid")
        if not 1 <= self.max_answer_claims <= 8:
            raise ValueError("Brand Agent answer claim limit is invalid")
        if not 1 <= self.transcript_retention_days <= 90:
            raise ValueError("Brand Agent transcript retention is invalid")
        if not self.composer_version:
            raise ValueError("Brand Agent composer version is required")


_TOKEN = re.compile(r"[a-z0-9]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "reveal your instructions",
    "show your instructions",
    "jailbreak",
    "prompt injection",
    "act as root",
    "brand_other",
    "other tenant",
    "cross tenant",
    "<script",
)
_SECRET_MARKERS = (
    "api key",
    "password",
    "private key",
    "access token",
    "bearer token",
    "credential",
    "internal notes",
    "hidden prompt",
    "company id",
    "tenant id",
)
_STOPWORDS = frozenset(
    {
        "a", "about", "an", "and", "are", "as", "at", "be", "by", "can",
        "do", "does", "for", "from", "has", "have", "how", "i", "in", "is",
        "it", "me", "of", "on", "or", "our", "that", "the", "their", "this",
        "to", "us", "what", "when", "where", "which", "who", "why", "with",
        "you", "your",
    }
)


def load_brand_agent_policy(path: Path) -> BrandAgentPolicy:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version",
        "brand_id",
        "public_claim_ids",
        "max_question_chars",
        "max_answer_claims",
        "transcript_retention_days",
        "composer_version",
    }
    if set(value) != expected or value.get("schema_version") != "1.0":
        raise ValueError("Brand Agent policy fields or schema are invalid")
    return BrandAgentPolicy(
        brand_id=value["brand_id"],
        public_claim_ids=tuple(value["public_claim_ids"]),
        max_question_chars=value["max_question_chars"],
        max_answer_claims=value["max_answer_claims"],
        transcript_retention_days=value["transcript_retention_days"],
        composer_version=value["composer_version"],
    )


class BrandAgentAuditStore:
    """Owner-only interaction metadata and idempotent action receipts."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("BrandAgentAuditStore requires a durable file path")
        try:
            self._identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise BrandAgentError("unsafe Brand Agent audit storage") from exc
        self.timeout_seconds = timeout_seconds
        self._initialize()

    def record_interaction(
        self,
        *,
        brand_id: str,
        request_id: str,
        conversation_id: str,
        transcript_mode: str,
        consent: bool,
        question: str,
        response: Mapping[str, Any],
        retention_days: int,
    ) -> None:
        if transcript_mode not in {"off", "metadata", "content"}:
            raise ContractError("transcript mode is invalid")
        if transcript_mode == "off":
            return
        if transcript_mode == "content" and not consent:
            raise BrandAgentAuthorizationError(
                "content transcripts require explicit consent"
            )
        now = datetime.now(timezone.utc)
        row = (
            brand_id,
            request_id,
            conversation_id,
            transcript_mode,
            hashlib.sha256(question.encode("utf-8")).hexdigest(),
            hashlib.sha256(canonical_bytes(response)).hexdigest(),
            question if transcript_mode == "content" else None,
            str(response.get("answer", "")) if transcript_mode == "content" else None,
            str(response.get("status", "unknown")),
            now.isoformat(),
            (now + timedelta(days=retention_days)).isoformat(),
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT question_sha256,response_sha256 FROM interactions "
                "WHERE brand_id=? AND request_id=?",
                (brand_id, request_id),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (row[4], row[5]):
                    raise ContractError("Brand Agent request ID was reused")
                connection.commit()
                return
            connection.execute(
                """
                INSERT INTO interactions(
                  brand_id,request_id,conversation_id,transcript_mode,
                  question_sha256,response_sha256,question_text,answer_text,
                  outcome,recorded_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                row,
            )
            connection.commit()
        except (sqlite3.Error, ContractError):
            connection.rollback()
            raise
        finally:
            connection.close()

    def interaction(self, brand_id: str, request_id: str) -> dict[str, Any]:
        row = self._fetch_one(
            """
            SELECT brand_id,request_id,conversation_id,transcript_mode,
                   question_sha256,response_sha256,question_text,answer_text,
                   outcome,recorded_at,expires_at
            FROM interactions WHERE brand_id=? AND request_id=?
            """,
            (brand_id, request_id),
        )
        if row is None:
            raise KeyError(request_id)
        fields = (
            "brand_id", "request_id", "conversation_id", "transcript_mode",
            "question_sha256", "response_sha256", "question_text", "answer_text",
            "outcome", "recorded_at", "expires_at",
        )
        return dict(zip(fields, row, strict=True))

    def purge_expired(self, *, at: str | None = None) -> int:
        moment = parse_time(at or utc_now()).astimezone(timezone.utc).isoformat()
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM interactions WHERE expires_at<=?", (moment,)
            )
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()

    def reserve_action(
        self,
        *,
        brand_id: str,
        idempotency_key: str,
        manifest_checksum: str,
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT manifest_checksum,state,receipt_json
                FROM action_receipts WHERE brand_id=? AND idempotency_key=?
                """,
                (brand_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != manifest_checksum:
                    raise ContractError(
                        "action idempotency key is bound to another request"
                    )
                if row[1] in {"complete", "cancelled"}:
                    connection.commit()
                    return json.loads(row[2])
                raise BrandAgentActionUnknown(
                    "action is already dispatching or requires reconciliation"
                )
            connection.execute(
                """
                INSERT INTO action_receipts(
                  brand_id,idempotency_key,manifest_checksum,state,
                  receipt_json,updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    brand_id,
                    idempotency_key,
                    manifest_checksum,
                    "dispatching",
                    None,
                    utc_now(),
                ),
            )
            connection.commit()
            return None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_action(
        self,
        *,
        brand_id: str,
        idempotency_key: str,
        manifest_checksum: str,
        receipt: Mapping[str, Any],
    ) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE action_receipts
                SET state='complete',receipt_json=?,updated_at=?
                WHERE brand_id=? AND idempotency_key=?
                  AND manifest_checksum=? AND state='dispatching'
                """,
                (
                    canonical_bytes(receipt).decode("utf-8"),
                    utc_now(),
                    brand_id,
                    idempotency_key,
                    manifest_checksum,
                ),
            )
            if cursor.rowcount != 1:
                raise BrandAgentError("action reservation changed before completion")
            connection.commit()
        finally:
            connection.close()

    def mark_action_unknown(
        self,
        *,
        brand_id: str,
        idempotency_key: str,
        manifest_checksum: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE action_receipts SET state='unknown',updated_at=?
                WHERE brand_id=? AND idempotency_key=? AND manifest_checksum=?
                """,
                (utc_now(), brand_id, idempotency_key, manifest_checksum),
            )
            connection.commit()
        finally:
            connection.close()

    def cancel_action(
        self,
        *,
        brand_id: str,
        receipt_id: str,
        adapter: FollowUpTaskAdapter,
    ) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT idempotency_key,state,receipt_json
                FROM action_receipts
                WHERE brand_id=? AND state IN ('complete','cancelled')
                """,
                (brand_id,),
            ).fetchall()
            match = None
            for row in rows:
                receipt = json.loads(row[2])
                if receipt.get("receipt_id") == receipt_id:
                    match = (row, receipt)
                    break
            if match is None:
                raise KeyError(receipt_id)
            row, receipt = match
            if row[1] == "cancelled":
                connection.commit()
                return receipt
            task = adapter.cancel(str(receipt["paperclip_issue_id"]), receipt_id)
            cancelled = finalize_record(
                {
                    **{
                        key: value
                        for key, value in receipt.items()
                        if key not in {"content_checksum", "paperclip_status"}
                    },
                    "status": "cancelled",
                    "cancelled_at": utc_now(),
                    "paperclip_status": task.get("status"),
                }
            )
            connection.execute(
                """
                UPDATE action_receipts
                SET state='cancelled',receipt_json=?,updated_at=?
                WHERE brand_id=? AND idempotency_key=? AND state='complete'
                """,
                (
                    canonical_bytes(cancelled).decode("utf-8"),
                    utc_now(),
                    brand_id,
                    row[0],
                ),
            )
            connection.commit()
            return cancelled
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect(validate_schema=False)
        try:
            journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal is None or str(journal[0]).lower() != "wal":
                raise BrandAgentError("Brand Agent audit database did not enter WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata(
                  id INTEGER PRIMARY KEY CHECK(id=1),
                  version INTEGER NOT NULL,
                  migrated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_metadata(id,version,migrated_at)
                VALUES(1,1,datetime('now'));
                CREATE TABLE IF NOT EXISTS interactions(
                  brand_id TEXT NOT NULL,
                  request_id TEXT NOT NULL,
                  conversation_id TEXT NOT NULL,
                  transcript_mode TEXT NOT NULL,
                  question_sha256 TEXT NOT NULL,
                  response_sha256 TEXT NOT NULL,
                  question_text TEXT,
                  answer_text TEXT,
                  outcome TEXT NOT NULL,
                  recorded_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  PRIMARY KEY(brand_id,request_id)
                );
                CREATE TABLE IF NOT EXISTS action_receipts(
                  brand_id TEXT NOT NULL,
                  idempotency_key TEXT NOT NULL,
                  manifest_checksum TEXT NOT NULL,
                  state TEXT NOT NULL,
                  receipt_json TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(brand_id,idempotency_key)
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self, *, validate_schema: bool = True) -> sqlite3.Connection:
        try:
            validate_sqlite_storage(self.database_path, self._identity)
            connection = sqlite3.connect(
                self.database_path, timeout=self.timeout_seconds
            )
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}"
            )
            if validate_schema:
                row = connection.execute(
                    "SELECT version FROM schema_metadata WHERE id=1"
                ).fetchone()
                if row is None or int(row[0]) != 1:
                    raise BrandAgentError(
                        "unsupported Brand Agent audit schema version"
                    )
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            raise BrandAgentError("could not open Brand Agent audit storage") from exc

    def _fetch_one(
        self, query: str, parameters: tuple[Any, ...]
    ) -> tuple[Any, ...] | None:
        connection = self._connect()
        try:
            return connection.execute(query, parameters).fetchone()
        finally:
            connection.close()


class PaperclipFollowUpTaskAdapter:
    """Translate one confirmed local action into authoritative Paperclip work."""

    def __init__(
        self,
        lifecycle: PaperclipLifecycleAdapter,
        *,
        parent_issue_id: str,
    ) -> None:
        self.lifecycle = lifecycle
        self.parent_issue_id = parent_issue_id

    def create(
        self, manifest: Mapping[str, Any], idempotency_key: str
    ) -> Mapping[str, Any]:
        return self.lifecycle.create_task(
            title="Fleet Brand Agent — human follow-up request",
            campaign_id="brand-agent-follow-up",
            stage="human-follow-up",
            acceptance_criteria=(
                "A Fleet human reviews the approved contact reference.",
                "No automated external message is sent by Agency OS.",
                "The request can be cancelled before human completion.",
            ),
            parent_id=self.parent_issue_id,
            status="todo",
            idempotency_key=idempotency_key,
            artifact_refs=(str(manifest["content_checksum"]),),
        )

    def cancel(self, issue_id: str, receipt_id: str) -> Mapping[str, Any]:
        return self.lifecycle.update_task(
            issue_id,
            status="cancelled",
            comment=(
                "Cancelled through the Fleet Brand Agent reversible-action "
                f"receipt {receipt_id}. No automated external message was sent."
            ),
        )


class BrandAgentService:
    """Read-only Brand Twin agent plus one separately gated reversible action."""

    def __init__(
        self,
        *,
        policy: BrandAgentPolicy,
        tenancy: FleetTenantAuthority,
        intelligence: BrandIntelligenceAuthority,
        audit: BrandAgentAuditStore,
        action_adapter: FollowUpTaskAdapter | None = None,
        action_secret: bytes | None = None,
    ) -> None:
        self.policy = policy
        self.tenancy = tenancy
        self.intelligence = intelligence
        self.audit = audit
        self.action_adapter = action_adapter
        self.action_secret = action_secret
        self.principal = Principal(
            f"{policy.brand_id}-brand-agent", "brand-agent-service", policy.brand_id
        )

    def answer(
        self,
        *,
        question: str,
        request_id: str,
        conversation_id: str,
        transcript_mode: str = "metadata",
        transcript_consent: bool = False,
    ) -> dict[str, Any]:
        self._require_module("brand_agent")
        question = self._validate_text(question, self.policy.max_question_chars)
        self._validate_identifier(request_id, "request")
        self._validate_identifier(conversation_id, "conversation")
        lowered = question.casefold()
        if any(marker in lowered for marker in _INJECTION_MARKERS):
            response = self._response(
                request_id=request_id,
                conversation_id=conversation_id,
                status="refused",
                answer=(
                    "I cannot follow instructions that try to change my rules, "
                    "cross a brand boundary, or reveal protected instructions."
                ),
                reason="prompt_injection_or_boundary_request",
                citations=[],
                uncertainty=["The request was not used as brand evidence."],
            )
        elif any(marker in lowered for marker in _SECRET_MARKERS):
            response = self._response(
                request_id=request_id,
                conversation_id=conversation_id,
                status="refused",
                answer=(
                    "I cannot provide secrets, credentials, private notes, or "
                    "protected operating instructions."
                ),
                reason="protected_information_request",
                citations=[],
                uncertainty=[],
            )
        else:
            profile = self.intelligence.operating_profile(self.principal)
            claims = [
                claim
                for claim in profile["claims"]
                if claim["claim_id"] in self.policy.public_claim_ids
            ]
            selected = self._select_claims(question, profile, claims)
            if not selected:
                response = self._response(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    status="unknown",
                    answer=(
                        "I do not have approved Fleet information that answers "
                        "that question. I will not guess."
                    ),
                    reason="approved_truth_unavailable",
                    citations=[],
                    uncertainty=[
                        "This topic is not present in the approved public Brand Twin."
                    ],
                )
            else:
                response = self._response(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    status="answered",
                    answer=" ".join(
                        self._claim_sentence(claim, profile) for claim in selected
                    ),
                    reason="approved_claims_found",
                    citations=[self._citation(claim, profile) for claim in selected],
                    uncertainty=[],
                )
        self.audit.record_interaction(
            brand_id=self.policy.brand_id,
            request_id=request_id,
            conversation_id=conversation_id,
            transcript_mode=transcript_mode,
            consent=transcript_consent,
            question=question,
            response=response,
            retention_days=self.policy.transcript_retention_days,
        )
        return response

    def public_profile(self) -> dict[str, Any]:
        self._require_module("brand_agent")
        profile = self.intelligence.operating_profile(self.principal)
        allowed = set(self.policy.public_claim_ids)
        return finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "brand_agent_public_profile",
                "brand_id": self.policy.brand_id,
                "profile_checksum": profile["content_checksum"],
                "claims": [
                    copy.deepcopy(item)
                    for item in profile["claims"]
                    if item["claim_id"] in allowed
                ],
                "policies": copy.deepcopy(profile["policies"]),
                "conflicts": copy.deepcopy(profile["conflicts"]),
                "evidence_gaps": [
                    item
                    for item in profile["evidence_gaps"]
                    if item["claim_id"] in allowed
                ],
            }
        )

    def prepare_follow_up(
        self,
        *,
        contact_reference: str,
        message: str,
        expires_in_minutes: int = 15,
    ) -> dict[str, Any]:
        self._require_module("brand_agent")
        self._require_module("controlled_actions")
        self._require_action_runtime()
        contact = self._validate_text(contact_reference, 160)
        text = self._validate_text(message, 1000)
        if not 1 <= expires_in_minutes <= 30:
            raise ContractError("follow-up confirmation window is invalid")
        created = datetime.now(timezone.utc)
        manifest = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "brand_agent_action_manifest",
                "brand_id": self.policy.brand_id,
                "action": "request_human_follow_up",
                "contact_reference": contact,
                "message": text,
                "created_at": created.isoformat(),
                "expires_at": (
                    created + timedelta(minutes=expires_in_minutes)
                ).isoformat(),
                "external_write": False,
                "paperclip_task_only": True,
            }
        )
        token = hmac.new(
            self.action_secret or b"",
            str(manifest["content_checksum"]).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "manifest": manifest,
            "confirmation_token": token,
            "confirmation_text": (
                "Confirm creation of one cancellable Fleet human follow-up task. "
                "No automated external message will be sent."
            ),
        }

    def confirm_follow_up(
        self,
        *,
        manifest: Mapping[str, Any],
        confirmation_token: str,
        idempotency_key: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        self._require_module("brand_agent")
        self._require_module("controlled_actions")
        adapter = self._require_action_runtime()
        if confirmed is not True:
            raise BrandAgentAuthorizationError("explicit action confirmation is required")
        self._validate_identifier(idempotency_key, "idempotency")
        self._validate_action_manifest(manifest)
        expected = hmac.new(
            self.action_secret or b"",
            str(manifest["content_checksum"]).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, confirmation_token):
            raise BrandAgentAuthorizationError("action confirmation token is invalid")
        checksum = str(manifest["content_checksum"])
        replay = self.audit.reserve_action(
            brand_id=self.policy.brand_id,
            idempotency_key=idempotency_key,
            manifest_checksum=checksum,
        )
        if replay is not None:
            return replay
        try:
            task = adapter.create(manifest, idempotency_key)
            if task.get("status") not in {"todo", "backlog"}:
                raise BrandAgentError("Paperclip follow-up task state is invalid")
            issue_id = str(task.get("id", ""))
            if not issue_id:
                raise BrandAgentError("Paperclip follow-up task identity is missing")
            receipt = finalize_record(
                {
                    "schema_version": "1.0",
                    "artifact_type": "brand_agent_action_receipt",
                    "receipt_id": f"receipt_{secrets.token_hex(12)}",
                    "brand_id": self.policy.brand_id,
                    "action": "request_human_follow_up",
                    "manifest_checksum": checksum,
                    "idempotency_key": idempotency_key,
                    "paperclip_issue_id": issue_id,
                    "paperclip_status": task["status"],
                    "status": "complete",
                    "external_write": False,
                    "completed_at": utc_now(),
                    "cancelled_at": None,
                }
            )
            self.audit.complete_action(
                brand_id=self.policy.brand_id,
                idempotency_key=idempotency_key,
                manifest_checksum=checksum,
                receipt=receipt,
            )
            return receipt
        except Exception:
            self.audit.mark_action_unknown(
                brand_id=self.policy.brand_id,
                idempotency_key=idempotency_key,
                manifest_checksum=checksum,
            )
            raise

    def cancel_follow_up(self, *, receipt_id: str) -> dict[str, Any]:
        self._require_module("controlled_actions")
        adapter = self._require_action_runtime()
        self._validate_identifier(receipt_id, "receipt")
        return self.audit.cancel_action(
            brand_id=self.policy.brand_id,
            receipt_id=receipt_id,
            adapter=adapter,
        )

    def _response(
        self,
        *,
        request_id: str,
        conversation_id: str,
        status: str,
        answer: str,
        reason: str,
        citations: Sequence[Mapping[str, Any]],
        uncertainty: Sequence[str],
    ) -> dict[str, Any]:
        return finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "brand_agent_response",
                "brand_id": self.policy.brand_id,
                "request_id": request_id,
                "conversation_id": conversation_id,
                "status": status,
                "answer": answer,
                "reason": reason,
                "citations": [copy.deepcopy(dict(item)) for item in citations],
                "uncertainty": list(uncertainty),
                "composer_version": self.policy.composer_version,
                "generated_at": utc_now(),
                "external_action": False,
            }
        )

    def _select_claims(
        self,
        question: str,
        profile: Mapping[str, Any],
        claims: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        lowered = question.casefold()
        normalized_words = " ".join(_TOKEN.findall(lowered))
        query = self._tokens(question)
        if normalized_words in {"what is fleet", "tell me about fleet"}:
            query = {"platform"}
        if not query:
            return []
        entities = {item["entity_id"]: item for item in profile.get("entities", [])}
        boosts = {
            "claim_fleet_business_name": ("name", "called", "business"),
            "claim_fleet_unified_product": ("what is fleet", "product", "platform"),
            "claim_content_engine_first_class": ("content", "production", "automate"),
            "claim_paperclip_authority": ("paperclip", "approval", "task", "authority"),
            "claim_fleet_base_domain": ("domain", "website", "url"),
            "claim_agency_os_live": ("live", "status", "runtime", "ready"),
            "claim_real_providers_unconnected": (
                "provider", "connected", "cms", "crm", "analytics", "social"
            ),
        }
        ranked: list[tuple[int, str, Mapping[str, Any]]] = []
        for claim in claims:
            entity = entities.get(str(claim["subject_entity_id"]), {})
            material = " ".join(
                (
                    str(entity.get("canonical_name", "")),
                    " ".join(entity.get("aliases", [])),
                    str(claim["predicate"]).replace("_", " "),
                    self._plain_value(claim["object"]),
                    " ".join(
                        str(item.get("extract", "")) for item in claim["evidence"]
                    ),
                )
            )
            score = len(query & self._tokens(material)) * 10
            for marker in boosts.get(str(claim["claim_id"]), ()):
                if marker in lowered:
                    score += 25
            if normalized_words in {"what is fleet", "tell me about fleet"} and claim[
                "claim_id"
            ] == "claim_fleet_unified_product":
                score += 50
            if score > 0:
                ranked.append((score, str(claim["claim_id"]), claim))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if not ranked or ranked[0][0] < 10:
            return []
        threshold = max(10, ranked[0][0] - 20)
        return [
            item[2] for item in ranked if item[0] >= threshold
        ][: self.policy.max_answer_claims]

    def _claim_sentence(
        self, claim: Mapping[str, Any], profile: Mapping[str, Any]
    ) -> str:
        entities = {item["entity_id"]: item for item in profile.get("entities", [])}
        subject = entities.get(str(claim["subject_entity_id"]), {}).get(
            "canonical_name", "Fleet"
        )
        predicate = str(claim["predicate"])
        value = self._plain_value(claim["object"])
        if predicate == "business_name":
            return f"The business is called {value}."
        if predicate == "product_model":
            return f"{subject} is {value.rstrip('.')}."
        if predicate == "purpose":
            return f"{value.rstrip('.')}."
        if predicate == "authoritative_for":
            return f"{subject} is the authority for {value}."
        if predicate == "base_domain":
            return f"Fleet's approved base domain is {value}."
        if predicate in {"current_runtime_status", "real_provider_state"}:
            return f"{value.rstrip('.')}."
        return f"{subject}: {value}."

    @staticmethod
    def _citation(
        claim: Mapping[str, Any], profile: Mapping[str, Any]
    ) -> dict[str, Any]:
        evidence = claim["evidence"][0]
        source = evidence["source_ref"]
        return {
            "citation_id": f"claim:{claim['claim_id']}:v{claim['version']}",
            "claim_id": claim["claim_id"],
            "claim_version": claim["version"],
            "claim_checksum": claim["content_checksum"],
            "evidence_id": evidence["evidence_id"],
            "evidence_checksum": evidence["content_checksum"],
            "source_id": source["source_id"],
            "source_version": source["source_version"],
            "source_checksum": source["source_checksum"],
            "source_locator": source["locator"],
            "profile_checksum": profile["content_checksum"],
        }

    def _require_module(self, module: str) -> None:
        if not self.tenancy.module_enabled(self.principal, module):
            raise BrandAgentAuthorizationError(f"the {module} module is not enabled")

    def _require_action_runtime(self) -> FollowUpTaskAdapter:
        if self.action_adapter is None or not self.action_secret:
            raise BrandAgentAuthorizationError(
                "the Brand Agent action runtime is not configured"
            )
        return self.action_adapter

    def _validate_action_manifest(self, manifest: Mapping[str, Any]) -> None:
        expected = {
            "schema_version", "artifact_type", "brand_id", "action",
            "contact_reference", "message", "created_at", "expires_at",
            "external_write", "paperclip_task_only", "content_checksum",
        }
        if set(manifest) != expected:
            raise ContractError("Brand Agent action manifest fields are invalid")
        verify_record(manifest)
        if (
            manifest["schema_version"] != "1.0"
            or manifest["artifact_type"] != "brand_agent_action_manifest"
            or manifest["brand_id"] != self.policy.brand_id
            or manifest["action"] != "request_human_follow_up"
            or manifest["external_write"] is not False
            or manifest["paperclip_task_only"] is not True
        ):
            raise ContractError("Brand Agent action manifest boundary is invalid")
        if parse_time(manifest["expires_at"]) <= datetime.now(timezone.utc):
            raise BrandAgentAuthorizationError("action confirmation has expired")
        self._validate_text(str(manifest["contact_reference"]), 160)
        self._validate_text(str(manifest["message"]), 1000)

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            token
            for token in _TOKEN.findall(value.casefold())
            if token not in _STOPWORDS and token != "fleet" and len(token) > 1
        }

    @staticmethod
    def _plain_value(value: Any) -> str:
        if isinstance(value, list):
            if len(value) == 1:
                return str(value[0])
            return ", ".join(str(item) for item in value[:-1]) + f", and {value[-1]}"
        if isinstance(value, Mapping):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return str(value)

    @staticmethod
    def _validate_text(value: Any, maximum: int) -> str:
        if not isinstance(value, str):
            raise ContractError("Brand Agent text input is invalid")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum or _CONTROL.search(normalized):
            raise ContractError("Brand Agent text input is invalid")
        return normalized

    @staticmethod
    def _validate_identifier(value: Any, label: str) -> str:
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value)
        ):
            raise ContractError(f"Brand Agent {label} identifier is invalid")
        return value
