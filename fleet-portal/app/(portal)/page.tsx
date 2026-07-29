import Link from "next/link";
import { Metric, PageIntro, SectionHeading, Status } from "@/components/ui";
import { portalSnapshot } from "@/lib/view-model";

export default function HomePage() {
  return <>
    <PageIntro eyebrow="Good afternoon" title="Your brand, moving with intent."
      description="A clear view of what Fleet understands, what needs your attention, and where the system is creating value." />
    <section className="metric-grid" aria-label="Fleet status">
      <Metric value="Live" label="Operating state" detail="Fleet DMA is the only production portal tenant" tone="green" />
      <Metric value="0" label="Decisions waiting" detail="Nothing is currently blocking the system" />
      <Metric value="8" label="Evidence-bound claims" detail="Derived from three approved Fleet sources" />
      <Metric value="5" label="Mission groups" detail="Controlled AI-market observation coverage" tone="amber" />
    </section>
    <section className="panel feature-panel">
      <div><span className="eyebrow accent">Current focus</span><h2>The Fleet product foundation is active.</h2>
        <p>Content production, Brand Twin intelligence, AI-market observation and the governed Brand Agent now work as one controlled system.</p>
        <Link className="primary-link" href="/brand">Explore the Brand Twin <span aria-hidden="true">→</span></Link></div>
      <div className="orbital" aria-hidden="true"><i /><i /><i /><strong>F</strong></div>
    </section>
    <SectionHeading kicker="Products" title="Your active Fleet system" />
    <section className="module-grid">
      {portalSnapshot.modules.map((module, index) => <article className="module-card" key={module.name}>
        <span className="module-number">0{index + 1}</span><Status tone="good">{module.state}</Status>
        <h3>{module.name}</h3><p>{module.detail}</p>
      </article>)}
    </section>
  </>;
}
