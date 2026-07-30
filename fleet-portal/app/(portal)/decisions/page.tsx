import { EmptyState, PageIntro, SectionHeading, Status } from "@/components/ui";
import { submitPaperclipDecision } from "@/app/actions";
import { ActionButton } from "@/components/action-button";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export const metadata = { title: "Decisions" };

export default async function DecisionsPage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  const pending = view.approvals.filter(item => item.state === "pending");
  return <><PageIntro eyebrow="Decisions" title="You stay in control."
    description="Every decision is presented with its exact evidence and current Paperclip checksum." />
    {pending.length === 0 ? <EmptyState title="Nothing needs your decision">No current Paperclip approval is waiting for this tenant.</EmptyState> :
      <section className="claim-list">{pending.map(approval => <article key={approval.approval_id}><div><SectionHeading kicker="Brand fact" title={approval.statement ?? "Evidence packet awaiting review"} />
        <p>Approval: {approval.approval_id}</p><p>Evidence checksum: {approval.review_checksum ?? "No candidate evidence is linked"}</p>
        <form action={submitPaperclipDecision} className="stack-form"><input type="hidden" name="approval_id" value={approval.approval_id} /><input type="hidden" name="expected_checksum" value={approval.approval_checksum} />
          <label>Your decision<select name="decision" required><option value="">Choose</option><option value="approve">Approve</option><option value="reject">Reject</option></select></label>
          <label>Decision note<textarea name="decision_note" required maxLength={1000} /></label><ActionButton>Record decision in Paperclip</ActionButton></form></div><Status>Pending</Status></article>)}</section>}
  </>;
}
