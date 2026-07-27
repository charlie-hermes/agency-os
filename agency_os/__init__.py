"""Agency OS fictional reference controls."""

from .capabilities import CapabilityError, CapabilityRegistry
from .contracts import (
    ContractError,
    canonical_checksum,
    finalize_record,
    make_capability_record,
    verify_record,
)
from .gateway import GatewayDenied, MockPublisher
from .gateway_host import ActionGatewayClient, fictional_runtime
from .platform_authority_host import (
    PlatformAuthorityClient,
    PlatformAuthorityUnavailable,
    TenantArtifactClient,
    TenantEvidenceClient,
)
from .platform_adapters import (
    ArtifactStoreError,
    EvidenceStoreError,
    FictionalBuzzAdapter,
    PlatformAdapterError,
    make_approver_policy,
    make_buzz_context_packet,
    make_evidence_record,
    make_paperclip_task,
)
from .runtime_security import (
    CredentialBrokerError,
    FictionalCredentialBroker,
    FictionalCredentialGrant,
    RuntimeIdentityError,
    fictional_credential_broker,
    fictional_credential_grant,
)
from .store import AuthorizationError, Principal, TenantStore

__all__ = [
    "ActionGatewayClient",
    "ArtifactStoreError",
    "AuthorizationError",
    "CapabilityError",
    "CapabilityRegistry",
    "ContractError",
    "GatewayDenied",
    "MockPublisher",
    "CredentialBrokerError",
    "FictionalCredentialBroker",
    "FictionalCredentialGrant",
    "FictionalBuzzAdapter",
    "EvidenceStoreError",
    "PlatformAuthorityClient",
    "PlatformAuthorityUnavailable",
    "PlatformAdapterError",
    "RuntimeIdentityError",
    "TenantArtifactClient",
    "TenantEvidenceClient",
    "fictional_credential_broker",
    "fictional_credential_grant",
    "fictional_runtime",
    "Principal",
    "TenantStore",
    "canonical_checksum",
    "finalize_record",
    "make_capability_record",
    "make_approver_policy",
    "make_buzz_context_packet",
    "make_evidence_record",
    "make_paperclip_task",
    "verify_record",
]
