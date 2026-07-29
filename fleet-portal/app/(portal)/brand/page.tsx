import { PageIntro, SectionHeading, Status } from "@/components/ui";

export const metadata = { title: "Brand" };

const claims = [
  ["Business identity", "Fleet is the client-facing business."],
  ["Unified product", "Content production and the Brand Operating System are one modular platform."],
  ["Content Engine", "Automated content production remains a first-class Fleet product."],
  ["Work authority", "Paperclip owns operational tasks, dependencies and approvals."],
  ["Internal tenant", "Fleet DMA is Fleet's live internal product tenant."],
  ["Portal domain", "Client experiences use approved madebyfleet.com hostnames."],
  ["Operating state", "The controlled Agency OS product foundation is live."],
  ["Provider boundary", "Public provider writes remain disconnected unless separately approved."],
] as const;

export default function BrandPage() {
  return <>
    <PageIntro eyebrow="Living Brand Twin" title="One trusted version of the brand."
      description="Approved facts and limitations that Fleet's people, agents and content workflows can use—always tied back to evidence." />
    <section className="brand-summary panel"><div><span className="metric-value">8</span><strong>active evidence-bound claims</strong></div><div><span className="metric-value">3</span><strong>approved source documents</strong></div><Status tone="good">No unsupported public claims</Status></section>
    <SectionHeading kicker="Approved knowledge" title="Current brand foundation" />
    <section className="claim-list">
      {claims.map(([label, claim], index) => <article key={label}><span className="claim-index">{String(index + 1).padStart(2, "0")}</span><div><h3>{label}</h3><p>{claim}</p></div><Status tone="good">Supported</Status></article>)}
    </section>
  </>;
}
