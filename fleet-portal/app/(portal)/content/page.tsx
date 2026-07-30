import { EmptyState, PageIntro, SectionHeading, Status } from "@/components/ui";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export const metadata = { title: "Content" };

export default async function ContentPage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  return <><PageIntro eyebrow="Content Engine" title="Content with a reason to exist."
    description="This catalogue is read from durable authority records. Fleet does not invent historical work." />
    <SectionHeading kicker="Durable catalogue" title="Current content" />
    {view.content.length === 0 ? <EmptyState title="No catalogue items yet">Approved content will appear here after it is materialised.</EmptyState> :
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Content catalogue"><table><thead><tr><th>Content</th><th>Type</th><th>State</th><th>Evidence</th></tr></thead><tbody>{view.content.map(item => <tr key={item.content_id}><td><strong>{item.title}</strong><small>{item.content_checksum}</small></td><td>{item.content_type}</td><td><Status tone="attention">{item.lifecycle_state}</Status></td><td>{item.source_checksum}</td></tr>)}</tbody></table></div>}
  </>;
}
