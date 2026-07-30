import Link from "next/link";
import { Metric, PageIntro, SectionHeading, Status } from "@/components/ui";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export default async function HomePage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  const claimCount = view.brand_profile?.claims.length ?? view.brand_twin_claims.length;
  const pending = view.approval_counts.pending ?? 0;
  const missions = view.observatory?.complete_run_count ?? 0;
  const activeModules = Object.values(view.modules).filter(Boolean).length;
  return <>
    <PageIntro eyebrow="Fleet workspace" title="Your brand, moving with intent."
      description="A live view of what Fleet understands, what needs your attention, and what the governed system has completed." />
    <section className="metric-grid" aria-label="Fleet status">
      <Metric value={view.lifecycle_state} label="Operating state" detail="Read from the current tenant authority" tone={view.lifecycle_state === "active" ? "green" : "amber"} />
      <Metric value={String(pending)} label="Decisions waiting" detail="Current unresolved Paperclip approvals" />
      <Metric value={String(claimCount)} label="Evidence-bound claims" detail="Current approved Brand Twin projection" />
      <Metric value={String(missions)} label="Observation runs" detail="Completed Observatory evidence runs" tone="amber" />
    </section>
    <section className="panel feature-panel"><div><span className="eyebrow accent">Current system</span><h2>{activeModules} Fleet modules are enabled.</h2>
      <p>Every number on this page is produced from the protected Fleet authorities. Unknown or unavailable information is shown as such.</p>
      <Link className="primary-link" href="/brand">Explore the Brand Twin <span aria-hidden="true">→</span></Link></div>
      <div className="orbital" aria-hidden="true"><i /><i /><i /><strong>F</strong></div></section>
    <SectionHeading kicker="Products" title="Your enabled Fleet system" />
    <section className="module-grid">{Object.entries(view.modules).map(([module, enabled], index) => <article className="module-card" key={module}>
      <span className="module-number">{String(index + 1).padStart(2, "0")}</span><Status tone={enabled ? "good" : "attention"}>{enabled ? "Enabled" : "Unavailable"}</Status>
      <h3>{module.replaceAll("_", " ")}</h3><p>Technical access is read from the current entitlement authority.</p>
    </article>)}</section>
  </>;
}
