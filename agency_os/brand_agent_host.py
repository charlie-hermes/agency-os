"""Loopback-only authenticated HTTP and web host for the Fleet Brand Agent."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .brand_agent import (
    BrandAgentActionUnknown,
    BrandAgentAuditStore,
    BrandAgentAuthorizationError,
    BrandAgentError,
    BrandAgentService,
    PaperclipFollowUpTaskAdapter,
    load_brand_agent_policy,
)
from .brand_agent_mcp import BrandAgentMCP
from .brand_intelligence import BrandIntelligenceAuthority
from .contracts import ContractError, canonical_bytes
from .fleet_tenancy import FleetTenantAuthority
from .integrations import (
    PaperclipBrandBinding,
    PaperclipHTTPTransport,
    PaperclipLifecycleAdapter,
)


class BrandAgentHostError(ValueError):
    """The Brand Agent host configuration or request is unsafe."""


class _RateLimiter:
    def __init__(self, *, limit: int = 60, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}

    def admit(self, key: str) -> bool:
        now = time.monotonic()
        floor = now - self.window_seconds
        with self._lock:
            recent = [item for item in self._requests.get(key, []) if item > floor]
            if len(recent) >= self.limit:
                self._requests[key] = recent
                return False
            recent.append(now)
            self._requests[key] = recent
            return True


_WEB = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Brand Agent</title>
<style>
:root{color-scheme:dark;--bg:#08100f;--panel:#101b19;--ink:#f4f7f2;--muted:#9eb0a9;--line:#263936;--lime:#c7ff61;--red:#ff8a8a}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 70% -20%,#254038,transparent 45%),var(--bg);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,sans-serif}.shell{max-width:900px;margin:auto;padding:56px 24px 80px}.eyebrow{color:var(--lime);font-size:13px;letter-spacing:.14em;text-transform:uppercase}.hero{font-size:clamp(38px,7vw,78px);line-height:.95;margin:14px 0 20px;max-width:760px}.sub{color:var(--muted);max-width:650px;font-size:18px}.panel{background:color-mix(in srgb,var(--panel) 94%,transparent);border:1px solid var(--line);border-radius:22px;padding:22px;margin-top:34px;box-shadow:0 30px 80px #0008}label{display:block;color:var(--muted);font-size:13px;margin:14px 0 7px}input,textarea,select{width:100%;background:#091310;color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:13px;font:inherit}textarea{min-height:112px;resize:vertical}.row{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:end}button{background:var(--lime);color:#10170c;border:0;border-radius:12px;padding:13px 19px;font-weight:750;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}.answer{margin-top:20px;padding:20px;border-radius:16px;background:#0a1512;border:1px solid var(--line);white-space:pre-wrap}.status{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--lime)}.error{color:var(--red)}.citations{margin-top:14px;color:var(--muted);font-size:13px}.privacy{display:flex;gap:10px;align-items:center;margin-top:14px}.privacy input{width:auto}.fine{color:var(--muted);font-size:12px;margin-top:18px}@media(max-width:620px){.row{grid-template-columns:1fr}.shell{padding-top:34px}}
</style>
</head>
<body><main class="shell">
<div class="eyebrow">Made by Fleet · Private preview</div>
<h1 class="hero">Ask Fleet.<br>Get approved truth.</h1>
<p class="sub">This Brand Agent answers only from Fleet's approved Brand Twin, shows its sources, and says when it does not know.</p>
<section class="panel" aria-label="Fleet Brand Agent">
<label for="key">Private access key</label><input id="key" type="password" autocomplete="off" placeholder="Access key">
<label for="question">Your question</label><textarea id="question" placeholder="What is Fleet?"></textarea>
<div class="privacy"><input id="consent" type="checkbox"><label for="consent" style="margin:0">Store this conversation text for up to 30 days</label></div>
<div class="row"><div class="fine">Without consent, only checksums and the result type are retained.</div><button id="ask">Ask Fleet</button></div>
<div id="result" class="answer" hidden></div>
</section>
<p class="fine">This is the private G2.5 component, not the future Fleet client portal. No external model or publication provider is connected.</p>
</main>
<script>
const key=document.querySelector('#key'),q=document.querySelector('#question'),ask=document.querySelector('#ask'),box=document.querySelector('#result'),consent=document.querySelector('#consent');
key.value=sessionStorage.getItem('fleetBrandAgentKey')||'';
function escapeText(v){return document.createTextNode(String(v))}
ask.addEventListener('click',async()=>{ask.disabled=true;box.hidden=false;box.replaceChildren(escapeText('Checking approved Fleet evidence…'));sessionStorage.setItem('fleetBrandAgentKey',key.value);try{const body={question:q.value,request_id:crypto.randomUUID(),conversation_id:sessionStorage.getItem('fleetConversation')||crypto.randomUUID(),transcript_mode:consent.checked?'content':'metadata',transcript_consent:consent.checked};sessionStorage.setItem('fleetConversation',body.conversation_id);const r=await fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key.value},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');const status=document.createElement('div');status.className='status';status.textContent=data.status;const answer=document.createElement('div');answer.textContent=data.answer;const cites=document.createElement('div');cites.className='citations';cites.textContent=data.citations.length?`Sources: ${data.citations.map(c=>c.source_locator).join(', ')}`:'No approved source answered this question.';box.replaceChildren(status,answer,cites)}catch(e){box.className='answer error';box.replaceChildren(escapeText(e.message))}finally{ask.disabled=false}});
</script></body></html>"""


def load_runtime_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version", "brand_id", "policy_path", "tenancy_database",
        "intelligence_database", "audit_database", "paperclip_base_url",
        "paperclip_company_id", "paperclip_parent_issue_id", "paperclip_token_env",
        "api_key_env", "action_secret_env", "allowed_hosts", "allowed_origins",
    }
    if set(value) != expected or value.get("schema_version") != "1.0":
        raise BrandAgentHostError("Brand Agent runtime config is invalid")
    if value.get("brand_id") != "brand_fleet":
        raise BrandAgentHostError("Brand Agent runtime brand is invalid")
    for key in (
        "policy_path", "tenancy_database", "intelligence_database", "audit_database"
    ):
        if not Path(value[key]).is_absolute():
            raise BrandAgentHostError("Brand Agent runtime paths must be absolute")
    if not value["allowed_hosts"] or not all(
        isinstance(item, str) and item for item in value["allowed_hosts"]
    ):
        raise BrandAgentHostError("Brand Agent allowed hosts are invalid")
    if not isinstance(value["allowed_origins"], list):
        raise BrandAgentHostError("Brand Agent allowed origins are invalid")
    return value


def build_service(config: Mapping[str, Any]) -> BrandAgentService:
    policy = load_brand_agent_policy(Path(config["policy_path"]))
    if policy.brand_id != config["brand_id"]:
        raise BrandAgentHostError("Brand Agent policy crossed the runtime brand")
    required_env = (
        config["paperclip_token_env"],
        config["api_key_env"],
        config["action_secret_env"],
    )
    if any(not os.environ.get(str(name)) for name in required_env):
        raise BrandAgentHostError("Brand Agent runtime secret is missing")
    transport = PaperclipHTTPTransport(
        base_url=config["paperclip_base_url"],
        bearer_token=os.environ[config["paperclip_token_env"]],
    )
    lifecycle = PaperclipLifecycleAdapter(
        transport,
        PaperclipBrandBinding(
            config["paperclip_company_id"], config["brand_id"]
        ),
    )
    return BrandAgentService(
        policy=policy,
        tenancy=FleetTenantAuthority(config["tenancy_database"]),
        intelligence=BrandIntelligenceAuthority(config["intelligence_database"]),
        audit=BrandAgentAuditStore(config["audit_database"]),
        action_adapter=PaperclipFollowUpTaskAdapter(
            lifecycle, parent_issue_id=config["paperclip_parent_issue_id"]
        ),
        action_secret=os.environ[config["action_secret_env"]].encode("utf-8"),
    )


def make_handler(
    *, service: BrandAgentService, config: Mapping[str, Any]
) -> type[BaseHTTPRequestHandler]:
    api_key = os.environ[str(config["api_key_env"])].encode("utf-8")
    allowed_hosts = frozenset(str(item).casefold() for item in config["allowed_hosts"])
    allowed_origins = frozenset(config["allowed_origins"])
    limiter = _RateLimiter()
    mcp = BrandAgentMCP(service)

    class Handler(BaseHTTPRequestHandler):
        server_version = "FleetBrandAgent/1"

        def do_GET(self) -> None:
            if not self._admitted_request(require_auth=False):
                return
            if self.path == "/health":
                try:
                    profile = service.public_profile()
                    self._json(
                        HTTPStatus.OK,
                        {
                            "status": "ok",
                            "brand_id": profile["brand_id"],
                            "public_claim_count": len(profile["claims"]),
                            "external_model": False,
                        },
                    )
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"status": "unavailable"},
                    )
                return
            if self.path == "/":
                body = _WEB.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._security_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._admitted_request(require_auth=True):
                return
            if not limiter.admit(f"{self.client_address[0]}:{self.path}"):
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate limit exceeded"})
                return
            try:
                payload = self._read_json()
                if self.path == "/api/answer":
                    self._exact(
                        payload,
                        {
                            "question", "request_id", "conversation_id",
                            "transcript_mode", "transcript_consent",
                        },
                        ("question", "request_id", "conversation_id"),
                    )
                    value = service.answer(
                        question=payload["question"],
                        request_id=payload["request_id"],
                        conversation_id=payload["conversation_id"],
                        transcript_mode=payload.get("transcript_mode", "metadata"),
                        transcript_consent=payload.get("transcript_consent", False),
                    )
                elif self.path == "/api/follow-up/prepare":
                    self._exact(payload, {"contact_reference", "message"}, ("contact_reference", "message"))
                    value = service.prepare_follow_up(
                        contact_reference=payload["contact_reference"],
                        message=payload["message"],
                    )
                elif self.path == "/api/follow-up/confirm":
                    self._exact(
                        payload,
                        {"manifest", "confirmation_token", "idempotency_key", "confirmed"},
                        ("manifest", "confirmation_token", "idempotency_key", "confirmed"),
                    )
                    value = service.confirm_follow_up(
                        manifest=payload["manifest"],
                        confirmation_token=payload["confirmation_token"],
                        idempotency_key=payload["idempotency_key"],
                        confirmed=payload["confirmed"],
                    )
                elif self.path == "/api/follow-up/cancel":
                    self._exact(payload, {"receipt_id"}, ("receipt_id",))
                    value = service.cancel_follow_up(receipt_id=payload["receipt_id"])
                elif self.path == "/mcp":
                    value = mcp.handle(payload, headers=dict(self.headers.items()))
                    if value is None:
                        self.send_response(HTTPStatus.ACCEPTED)
                        self._security_headers()
                        self.end_headers()
                        return
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, value)
            except BrandAgentAuthorizationError as exc:
                self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
            except BrandAgentActionUnknown as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
            except (ContractError, BrandAgentHostError, KeyError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except BrandAgentError:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Brand Agent unavailable"})

        def _admitted_request(self, *, require_auth: bool) -> bool:
            host = self.headers.get("Host", "").split(":", 1)[0].casefold()
            if host not in allowed_hosts:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "host not allowed"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self._json(HTTPStatus.FORBIDDEN, {"error": "origin not allowed"})
                return False
            if require_auth:
                authorization = self.headers.get("Authorization", "")
                expected = b"Bearer " + api_key
                if not hmac.compare_digest(authorization.encode("utf-8"), expected):
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return False
            return True

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise BrandAgentHostError("invalid content length") from exc
            if length < 2 or length > 65536:
                raise BrandAgentHostError("request body size is invalid")
            if self.headers.get_content_type() != "application/json":
                raise BrandAgentHostError("content type must be application/json")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise BrandAgentHostError("request body must be an object")
            return value

        @staticmethod
        def _exact(value: Mapping[str, Any], allowed: set[str], required: tuple[str, ...]) -> None:
            if set(value) - allowed or not set(required).issubset(value):
                raise BrandAgentHostError("request fields are invalid")

        def _json(self, status: HTTPStatus, value: Any) -> None:
            body = canonical_bytes(value)
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def serve(config_path: Path, *, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "::1"}:
        raise BrandAgentHostError("Brand Agent must bind to loopback")
    if not 1 <= port <= 65535:
        raise BrandAgentHostError("Brand Agent port is invalid")
    config = load_runtime_config(config_path)
    service = build_service(config)
    server = ThreadingHTTPServer((host, port), make_handler(service=service, config=config))
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fleet governed Brand Agent host")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3181)
    args = parser.parse_args()
    serve(args.config, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
