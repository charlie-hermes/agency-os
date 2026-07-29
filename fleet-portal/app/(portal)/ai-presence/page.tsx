import { Metric, PageIntro, SectionHeading, Status } from "@/components/ui";
import { portalSnapshot } from "@/lib/view-model";

export const metadata = { title: "AI presence" };

export default function AIPresencePage() {
  return <>
    <PageIntro eyebrow="AI Market Observatory" title="See how AI understands the brand."
      description="Repeatable customer missions reveal gaps, errors and opportunities without pretending that one test is a permanent ranking." />
    <section className="metric-grid compact"><Metric value={String(portalSnapshot.aiPresence.missions)} label="Mission groups" detail="Versioned internal observation coverage" /><Metric value="100%" label="Evidence retained" detail="Prompts, observations and evaluation checksums" tone="green" /><Metric value="0" label="Unsupported causal claims" detail="Findings remain honest about uncertainty" /></section>
    <section className="split-grid"><div className="panel"><SectionHeading kicker="Current finding" title="What the evidence supports" /><p className="large-copy">{portalSnapshot.aiPresence.finding}</p><Status tone="good">Grounded internal result</Status></div><div className="panel"><SectionHeading kicker="Important limit" title="What this does not mean" /><p className="large-copy">{portalSnapshot.aiPresence.limitation}</p><Status>Interpret with context</Status></div></section>
  </>;
}
