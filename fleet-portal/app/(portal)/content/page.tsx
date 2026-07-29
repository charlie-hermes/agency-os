import { PageIntro, SectionHeading, Status } from "@/components/ui";
import { portalSnapshot } from "@/lib/view-model";

export const metadata = { title: "Content" };

export default function ContentPage() {
  return <>
    <PageIntro eyebrow="Content Engine" title="Content with a reason to exist."
      description="Every item begins with evidence, moves through specialist production and QA, and stays bound to the exact version you approve." />
    <SectionHeading kicker="Durable catalogue" title="Current content" />
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Content catalogue"><table><thead><tr><th>Content</th><th>Type</th><th>State</th><th>Evidence</th></tr></thead><tbody>
      {portalSnapshot.content.map(item => <tr key={item.id}><td><strong>{item.title}</strong><small>{item.note}</small></td><td>{item.type}</td><td><Status tone="attention">{item.state}</Status></td><td>Checksummed source</td></tr>)}
    </tbody></table></div>
    <p className="evidence-note">This catalogue starts with one controlled Fleet item. Earlier workflow proofs remain in their original authorities and have not been presented as client history.</p>
  </>;
}
