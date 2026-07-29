export function PageIntro({ eyebrow, title, description, aside }: {
  eyebrow: string; title: string; description: string; aside?: React.ReactNode;
}) {
  return <section className="page-intro"><div><span className="eyebrow accent">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{aside}</section>;
}

export function Metric({ value, label, detail, tone = "ink" }: {
  value: string; label: string; detail: string; tone?: "ink" | "green" | "amber";
}) {
  return <article className={`metric ${tone}`}><span className="metric-value">{value}</span><strong>{label}</strong><p>{detail}</p></article>;
}

export function SectionHeading({ kicker, title, action }: { kicker?: string; title: string; action?: React.ReactNode }) {
  return <div className="section-heading"><div>{kicker && <span className="eyebrow">{kicker}</span>}<h2>{title}</h2></div>{action}</div>;
}

export function Status({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "good" | "attention" | "neutral" }) {
  return <span className={`status ${tone}`}><i />{children}</span>;
}

export function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="empty-state"><span aria-hidden="true">◇</span><h3>{title}</h3><p>{children}</p></div>;
}
