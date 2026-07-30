import Link from "next/link";
import { PageIntro, SectionHeading, Status } from "@/components/ui";
import { authorityCall } from "@/lib/authority";
import { requireFleetAdministrator } from "@/lib/identity";

export const dynamic = "force-dynamic";
const sections = ["accounts", "brands", "tenants", "provisioning", "users", "entitlements", "support", "health", "audit"];

type AdminProjection = {
  brand_id: string;
  tenant: null | Record<string, string | number>;
  provisioning: null | { state: string; steps: Array<{ step_key: string; state: string; evidence_checksum: string | null }> };
  portal_counts: Record<string, number>;
  audit: Array<{ sequence: number; actor_id: string; operation: string; target_id: string; outcome: string; recorded_at: string }>;
};

export default async function AdminPage({ params }: { params: Promise<{ section?: string[] }> }) {
  const administrator = await requireFleetAdministrator();
  const value = await params;
  const section = value.section?.[0] ?? "accounts";
  if (!sections.includes(section)) throw new Error("Unknown Fleet administration section.");
  const view = await authorityCall<AdminProjection>({ operation: "admin_projection", admin_subject: administrator.userId });
  const tenantState = String(view.tenant?.lifecycle_state ?? "unavailable");
  const sectionData: Record<string, unknown> = section === "audit" ? { events: view.audit } :
    section === "provisioning" ? { provisioning: view.provisioning } :
    section === "health" ? { tenant_state: tenantState, ...view.portal_counts } :
    section === "users" ? { memberships: view.portal_counts.memberships, active: view.portal_counts.active_memberships } :
    section === "tenants" || section === "accounts" || section === "brands" ? { tenant: view.tenant } :
    { tenant: view.tenant, operational_counts: view.portal_counts };
  return <div className="admin-shell"><aside><Link className="brandmark" href="/admin"><span>FLEET / ADMIN</span></Link><nav>{sections.map(item => <Link href={`/admin/${item}`} key={item}>{item.replaceAll("-", " ")}</Link>)}</nav><a href="https://fleet.madebyfleet.com/">Open client view →</a></aside><main className="admin-main"><PageIntro eyebrow="Fleet administration" title={section[0].toUpperCase() + section.slice(1)} description="Fleet-only operational data from the protected tenant and portal authorities." />
    <section className="metric-grid compact"><article className="metric green"><span className="metric-value">{tenantState}</span><strong>Tenant state</strong><p>{view.brand_id}</p></article><article className="metric"><span className="metric-value">{view.portal_counts.pending_approvals ?? 0}</span><strong>Pending approvals</strong><p>Current Paperclip decisions</p></article><article className="metric"><span className="metric-value">{view.portal_counts.unknown_commands ?? 0}</span><strong>Unknown outcomes</strong><p>Require automatic reconciliation</p></article></section>
    <section className="panel"><SectionHeading kicker="Current authority state" title={`${section} projection`} /><pre>{JSON.stringify(sectionData, null, 2)}</pre><Status tone={tenantState === "active" ? "good" : "attention"}>Read from current authority</Status></section>
  </main></div>;
}
