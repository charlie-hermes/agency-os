import { EmptyState, PageIntro, SectionHeading, Status } from "@/components/ui";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export const metadata = { title: "Brand" };

export default async function BrandPage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  const claims = view.brand_profile?.claims ?? [];
  const sources = view.brand_profile?.sources.length ?? 0;
  return <><PageIntro eyebrow="Living Brand Twin" title="One trusted version of the brand."
    description="These are the current approved claims returned by Fleet's protected Brand Intelligence authority." />
    <section className="brand-summary panel"><div><span className="metric-value">{claims.length}</span><strong>active evidence-bound claims</strong></div><div><span className="metric-value">{sources}</span><strong>approved sources</strong></div>
      <Status tone={(view.brand_profile?.conflicts.length ?? 0) === 0 ? "good" : "attention"}>{view.brand_profile?.conflicts.length ?? 0} unresolved conflicts</Status></section>
    <SectionHeading kicker="Approved knowledge" title="Current brand foundation" />
    {claims.length === 0 ? <EmptyState title="No approved claims are available">Fleet will show claims only after evidence and Paperclip approval are both present.</EmptyState> :
      <section className="claim-list">{claims.map((claim, index) => <article key={claim.claim_id}><span className="claim-index">{String(index + 1).padStart(2, "0")}</span><div><h3>{claim.predicate.replaceAll("_", " ")}</h3><p>{typeof claim.object === "string" ? claim.object : JSON.stringify(claim.object)}</p><small>{claim.evidence.length} evidence record(s) · {claim.content_checksum}</small></div><Status tone="good">Approved</Status></article>)}</section>}
  </>;
}
