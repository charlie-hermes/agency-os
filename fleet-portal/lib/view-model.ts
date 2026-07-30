import "server-only";

import { authorityCall } from "@/lib/authority";
import type { PortalContext, PortalProjection } from "@/lib/types";

const fixtureProjection: PortalProjection = {
  tenant_id: "tenant_fleet",
  brand_id: "brand_fleet",
  lifecycle_state: "active",
  modules: {
    content_engine: true,
    brand_twin: true,
    ai_market_observatory: true,
    brand_agent: true,
    controlled_actions: true,
    client_portal: true,
  },
  source_counts: { admitted: 3 },
  candidate_counts: { approved: 8 },
  approval_counts: {},
  brand_twin_claims: [],
  brand_profile: {
    sources: [{ source_id: "fixture_source" }, { source_id: "fixture_source_2" }, { source_id: "fixture_source_3" }],
    claims: Array.from({ length: 8 }, (_, index) => ({
      claim_id: `fixture_claim_${index + 1}`,
      predicate: `Approved claim ${index + 1}`,
      object: "Fixture-only browser acceptance evidence",
      content_checksum: `sha256:${String(index + 1).padStart(64, "0")}`,
      evidence: [{ evidence_id: `fixture_evidence_${index + 1}` }],
    })),
    conflicts: [],
    evidence_gaps: [],
  },
  observatory: {
    complete_run_count: 2,
    observation_count: 40,
    finding_count: 1,
    external_ai_coverage: "unknown",
    limitations: ["Fixture data is used only by local browser acceptance."],
  },
  content: [{
    content_id: "content_fixture",
    title: "Fleet AI readiness introduction",
    content_type: "article",
    lifecycle_state: "controlled_preview",
    source_checksum: `sha256:${"1".repeat(64)}`,
    content_checksum: `sha256:${"2".repeat(64)}`,
    created_at: "2026-07-29T12:00:00Z",
  }],
  sources: [],
  candidates: [],
  approvals: [],
  invitations: [],
  memberships: [{
    membership_id: "membership_fixture",
    workos_subject: "user_fleet_fixture",
    client_role: "owner",
    approval_scopes: ["brand_fact", "claim", "content", "publication", "access_change"],
    state: "active",
    updated_at: "2026-07-29T12:00:00Z",
  }],
};

export async function getPortalProjection(context: PortalContext): Promise<PortalProjection> {
  const fixture = process.env.FLEET_PORTAL_IDENTITY_MODE === "fixture" &&
    process.env.NODE_ENV !== "production" &&
    process.env.FLEET_PORTAL_FIXTURE_ACK === "local-test-only";
  if (fixture) return fixtureProjection;
  return authorityCall<PortalProjection>({
    operation: "portal_projection",
    workos_subject: context.workos_subject,
    workos_organization_id: context.workos_organization_id,
    hostname: context.hostname,
    origin: context.origin,
    access_identity_verified: true,
    session_id: context.session_id,
    correlation_id: context.correlation_id,
  });
}
