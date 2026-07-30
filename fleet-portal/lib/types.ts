export type PortalContext = {
  hostname: string;
  origin: string;
  workos_subject: string;
  workos_organization_id: string;
  customer_account_id: string;
  client_brand_id: string;
  tenant_id: string;
  brand_id: string;
  client_role: "owner" | "approver" | "contributor" | "analyst" | "viewer";
  approval_scopes: string[];
  session_id: string;
  entitlement_version: number;
  correlation_id: string;
};

export type AuthorityIdentity = {
  workos_subject: string;
  workos_organization_id: string;
  hostname: string;
  origin: string;
  access_identity_verified: true;
  session_id: string;
  correlation_id: string;
};


export type PortalProjection = {
  tenant_id: string;
  brand_id: string;
  lifecycle_state: string;
  modules: Record<string, boolean>;
  source_counts: Record<string, number>;
  candidate_counts: Record<string, number>;
  approval_counts: Record<string, number>;
  brand_twin_claims: Array<Record<string, unknown>>;
  brand_profile: null | {
    sources: Array<Record<string, unknown>>;
    claims: Array<{
      claim_id: string;
      predicate: string;
      object: unknown;
      content_checksum: string;
      evidence: Array<Record<string, unknown>>;
    }>;
    conflicts: Array<Record<string, unknown>>;
    evidence_gaps: Array<Record<string, unknown>>;
  };
  observatory: null | {
    complete_run_count: number;
    observation_count: number;
    finding_count: number;
    external_ai_coverage: string;
    limitations: string[];
  };
  content: Array<{
    content_id: string;
    title: string;
    content_type: string;
    lifecycle_state: string;
    source_checksum: string;
    content_checksum: string;
    created_at: string;
  }>;
  sources: Array<Record<string, unknown>>;
  candidates: Array<{
    candidate_id: string;
    candidate_checksum: string;
    statement: string;
    source_locator: string;
    state: string;
    reviewed_statement: string | null;
  }>;
  approvals: Array<{
    approval_id: string;
    approval_checksum: string;
    state: string;
    candidate_id: string | null;
    statement: string | null;
    review_checksum: string | null;
    source: Record<string, unknown> | null;
    updated_at: string;
  }>;
  invitations: Array<{
    invitation_id: string;
    email: string;
    client_role: string;
    approval_scopes: string[];
    state: string;
    expires_at: string;
    created_at: string;
  }>;
  memberships: Array<{
    membership_id: string;
    workos_subject: string;
    client_role: string;
    approval_scopes: string[];
    state: string;
    updated_at: string;
  }>;
};
