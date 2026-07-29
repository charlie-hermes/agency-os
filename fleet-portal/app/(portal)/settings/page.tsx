import { PageIntro, SectionHeading, Status } from "@/components/ui";

export const metadata = { title: "Settings" };

export default function SettingsPage() {
  return <>
    <PageIntro eyebrow="Settings" title="Access and service, made clear."
      description="See the active Fleet package and your access. Sensitive changes become requests for Fleet review; they never silently alter the system." />
    <section className="split-grid"><div className="panel"><SectionHeading kicker="Plan" title="Fleet Brand OS" /><p>Content Engine, Brand Twin, AI Market Observatory, Brand Agent and controlled actions.</p><Status tone="good">Internal zero-value order active</Status><button className="secondary-button" type="button">Request a plan conversation</button></div>
      <div className="panel"><SectionHeading kicker="Your access" title="Fleet owner" /><dl className="definition-list"><div><dt>Role</dt><dd>Owner</dd></div><div><dt>Authentication</dt><dd>WorkOS AuthKit</dd></div><div><dt>Network</dt><dd>Cloudflare Access</dd></div></dl></div></section>
    <section className="panel"><SectionHeading kicker="People" title="Workspace access" /><div className="person-row"><span className="avatar">F</span><div><strong>Fleet owner</strong><p>Owner · brand facts, claims, content, publication and access decisions</p></div><Status tone="good">Active</Status></div></section>
  </>;
}
