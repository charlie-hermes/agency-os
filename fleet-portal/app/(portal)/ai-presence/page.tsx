import { EmptyState, Metric, PageIntro, SectionHeading, Status } from "@/components/ui";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export const metadata = { title: "AI presence" };

export default async function AIPresencePage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  const observation = view.observatory;
  return <><PageIntro eyebrow="AI Market Observatory" title="See how AI understands the brand."
    description="Versioned observations are shown with their limits. Unknown external-AI coverage remains unknown." />
    {!observation ? <EmptyState title="No Observatory projection is available">The portal could not obtain current approved observation evidence.</EmptyState> : <>
      <section className="metric-grid compact"><Metric value={String(observation.complete_run_count)} label="Complete runs" detail="Versioned evidence runs" /><Metric value={String(observation.observation_count)} label="Observations" detail="Active retained observations" tone="green" /><Metric value={String(observation.finding_count)} label="Findings" detail="Current active findings" /></section>
      <section className="panel"><SectionHeading kicker="Evidence boundary" title={`External AI coverage: ${observation.external_ai_coverage}`} />{observation.limitations.map(item => <p className="large-copy" key={item}>{item}</p>)}<Status>Interpret with context</Status></section></>}
  </>;
}
