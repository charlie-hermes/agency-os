"""Stateless, versioned MCP surface for the governed Fleet Brand Agent."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from .brand_agent import (
    BrandAgentAuthorizationError,
    BrandAgentError,
    BrandAgentService,
)
from .contracts import ContractError, canonical_bytes


class BrandAgentMCPError(ValueError):
    """An MCP request did not satisfy the admitted protocol surface."""


class BrandAgentMCP:
    """Small stateless MCP server core; HTTP transport is provided separately."""

    protocol_version = "2025-11-25"
    server_name = "fleet-governed-brand-agent"
    server_version = "1.0.0"

    def __init__(self, service: BrandAgentService) -> None:
        self.service = service

    def handle(
        self,
        request: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(request, Mapping):
            raise BrandAgentMCPError("MCP request must be an object")
        if request.get("jsonrpc") != "2.0" or not isinstance(
            request.get("method"), str
        ):
            return self._error(request.get("id"), -32600, "Invalid Request")
        allowed = {"jsonrpc", "id", "method", "params"}
        if set(request) - allowed:
            return self._error(request.get("id"), -32600, "Invalid Request")
        method = str(request["method"])
        params = request.get("params", {})
        if not isinstance(params, Mapping):
            return self._error(request.get("id"), -32602, "Invalid params")
        try:
            self._validate_headers(method, params, headers or {})
            if method == "notifications/initialized":
                return None
            if "id" not in request:
                return None
            if method == "initialize":
                result = self._initialize(params)
            elif method == "resources/list":
                self._exact(params, {"cursor"}, required=())
                result = {"resources": self.resources()}
            elif method == "resources/read":
                self._exact(params, {"uri"}, required=("uri",))
                result = self.read_resource(str(params["uri"]))
            elif method == "tools/list":
                self._exact(params, {"cursor"}, required=())
                result = {"tools": self.tools()}
            elif method == "tools/call":
                self._exact(params, {"name", "arguments"}, required=("name",))
                arguments = params.get("arguments", {})
                if not isinstance(arguments, Mapping):
                    raise ContractError("tool arguments must be an object")
                result = self.call_tool(str(params["name"]), arguments)
            elif method == "ping":
                self._exact(params, set(), required=())
                result = {}
            else:
                return self._error(request.get("id"), -32601, "Method not found")
            return {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (ContractError, BrandAgentAuthorizationError, BrandAgentError) as exc:
            if method == "tools/call" and "id" in request:
                result = {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                }
                return {"jsonrpc": "2.0", "id": request["id"], "result": result}
            return self._error(request.get("id"), -32602, str(exc))
        except (KeyError, ValueError, TypeError):
            return self._error(request.get("id"), -32602, "Invalid params")

    def resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "fleet://brand/v1/claims",
                "name": "Fleet approved public claims",
                "description": "Active approved claims admitted for Brand Agent answers.",
                "mimeType": "application/json",
            },
            {
                "uri": "fleet://brand/v1/policies",
                "name": "Fleet Brand Agent policies",
                "description": "Active Brand Twin policies governing answers and actions.",
                "mimeType": "application/json",
            },
            {
                "uri": "fleet://brand/v1/profile",
                "name": "Fleet public Brand Agent profile",
                "description": "Checksummed public projection of the approved Fleet Brand Twin.",
                "mimeType": "application/json",
            },
        ]

    def read_resource(self, uri: str) -> dict[str, Any]:
        profile = self.service.public_profile()
        if uri == "fleet://brand/v1/profile":
            value: Any = profile
        elif uri == "fleet://brand/v1/claims":
            value = {
                "schema_version": "1.0",
                "brand_id": profile["brand_id"],
                "profile_checksum": profile["content_checksum"],
                "claims": profile["claims"],
            }
        elif uri == "fleet://brand/v1/policies":
            value = {
                "schema_version": "1.0",
                "brand_id": profile["brand_id"],
                "profile_checksum": profile["content_checksum"],
                "policies": profile["policies"],
            }
        else:
            raise ContractError("unknown Brand Agent resource")
        text = canonical_bytes(value).decode("utf-8")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": text,
                }
            ]
        }

    def tools(self) -> list[dict[str, Any]]:
        object_schema = "https://json-schema.org/draft/2020-12/schema"
        return [
            {
                "name": "fleet_brand_answer",
                "title": "Ask the governed Fleet Brand Agent",
                "description": (
                    "Answer from active approved Fleet claims with exact citations; "
                    "refuse unsupported or protected requests."
                ),
                "inputSchema": {
                    "$schema": object_schema,
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": "string", "minLength": 1, "maxLength": 1200},
                        "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
                        "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
                        "transcript_mode": {
                            "type": "string",
                            "enum": ["off", "metadata", "content"],
                            "default": "metadata",
                        },
                        "transcript_consent": {"type": "boolean", "default": False},
                    },
                    "required": ["question", "request_id", "conversation_id"],
                },
                "outputSchema": {
                    "$schema": object_schema,
                    "type": "object",
                    "required": [
                        "brand_id", "status", "answer", "citations", "content_checksum"
                    ],
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "fleet_follow_up_cancel",
                "title": "Cancel a Fleet human follow-up",
                "description": "Cancel one follow-up task by its exact action receipt.",
                "inputSchema": {
                    "$schema": object_schema,
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "receipt_id": {"type": "string", "minLength": 1, "maxLength": 128}
                    },
                    "required": ["receipt_id"],
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "fleet_follow_up_confirm",
                "title": "Confirm a Fleet human follow-up",
                "description": (
                    "After explicit human confirmation, create exactly one cancellable "
                    "Paperclip follow-up task. No external message is sent."
                ),
                "inputSchema": {
                    "$schema": object_schema,
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "manifest": {"type": "object"},
                        "confirmation_token": {"type": "string", "minLength": 64, "maxLength": 64},
                        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                    "required": [
                        "manifest", "confirmation_token", "idempotency_key", "confirmed"
                    ],
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "fleet_follow_up_prepare",
                "title": "Prepare a Fleet human follow-up",
                "description": (
                    "Prepare a short-lived exact manifest for human review; this does "
                    "not create a task or send an external message."
                ),
                "inputSchema": {
                    "$schema": object_schema,
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "contact_reference": {"type": "string", "minLength": 1, "maxLength": 160},
                        "message": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    "required": ["contact_reference", "message"],
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name == "fleet_brand_answer":
            self._exact(
                arguments,
                {
                    "question", "request_id", "conversation_id",
                    "transcript_mode", "transcript_consent",
                },
                required=("question", "request_id", "conversation_id"),
            )
            value = self.service.answer(
                question=arguments["question"],
                request_id=arguments["request_id"],
                conversation_id=arguments["conversation_id"],
                transcript_mode=arguments.get("transcript_mode", "metadata"),
                transcript_consent=arguments.get("transcript_consent", False),
            )
        elif name == "fleet_follow_up_prepare":
            self._exact(
                arguments,
                {"contact_reference", "message"},
                required=("contact_reference", "message"),
            )
            value = self.service.prepare_follow_up(
                contact_reference=arguments["contact_reference"],
                message=arguments["message"],
            )
        elif name == "fleet_follow_up_confirm":
            self._exact(
                arguments,
                {"manifest", "confirmation_token", "idempotency_key", "confirmed"},
                required=(
                    "manifest", "confirmation_token", "idempotency_key", "confirmed"
                ),
            )
            value = self.service.confirm_follow_up(
                manifest=arguments["manifest"],
                confirmation_token=arguments["confirmation_token"],
                idempotency_key=arguments["idempotency_key"],
                confirmed=arguments["confirmed"],
            )
        elif name == "fleet_follow_up_cancel":
            self._exact(arguments, {"receipt_id"}, required=("receipt_id",))
            value = self.service.cancel_follow_up(receipt_id=arguments["receipt_id"])
        else:
            raise ContractError("unknown Brand Agent tool")
        structured = copy.deepcopy(dict(value))
        return {
            "content": [
                {
                    "type": "text",
                    "text": canonical_bytes(structured).decode("utf-8"),
                }
            ],
            "structuredContent": structured,
            "isError": False,
        }

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self._exact(
            params,
            {"protocolVersion", "capabilities", "clientInfo"},
            required=("protocolVersion", "capabilities", "clientInfo"),
        )
        if not isinstance(params["capabilities"], Mapping) or not isinstance(
            params["clientInfo"], Mapping
        ):
            raise ContractError("MCP initialize parameters are invalid")
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"resources": {}, "tools": {}},
            "serverInfo": {"name": self.server_name, "version": self.server_version},
            "instructions": (
                "Use only approved Fleet resources. Show citations. Never infer "
                "unknown facts. Follow-up confirmation never sends an external message."
            ),
        }

    @staticmethod
    def _validate_headers(
        method: str, params: Mapping[str, Any], headers: Mapping[str, str]
    ) -> None:
        normalized = {key.casefold(): value for key, value in headers.items()}
        mirrored_method = normalized.get("mcp-method")
        if mirrored_method is not None and mirrored_method != method:
            raise ContractError("Mcp-Method header does not match request")
        expected_name = None
        if method == "tools/call":
            expected_name = params.get("name")
        elif method == "resources/read":
            expected_name = params.get("uri")
        mirrored_name = normalized.get("mcp-name")
        if mirrored_name is not None and mirrored_name != expected_name:
            raise ContractError("Mcp-Name header does not match request")

    @staticmethod
    def _exact(
        value: Mapping[str, Any],
        allowed: set[str],
        *,
        required: tuple[str, ...],
    ) -> None:
        if set(value) - allowed or not set(required).issubset(value):
            raise ContractError("request fields are invalid")

    @staticmethod
    def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": code, "message": message},
        }
