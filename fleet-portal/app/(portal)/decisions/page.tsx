import { EmptyState, PageIntro, SectionHeading } from "@/components/ui";
import { submitPaperclipDecision } from "@/app/actions";
import { ActionButton } from "@/components/action-button";

export const metadata = { title: "Decisions" };

export default function DecisionsPage() {
  const approvalId = process.env.FLEET_PORTAL_ACTIVE_APPROVAL_ID;
  const checksum = process.env.FLEET_PORTAL_ACTIVE_APPROVAL_CHECKSUM;
  return <>
    <PageIntro eyebrow="Decisions" title="You stay in control."
      description="Fleet can prepare work and explain the evidence. Important decisions remain yours, recorded against the exact item you reviewed." />
    {!approvalId || !checksum ? <EmptyState title="Nothing needs your decision">When a fact, claim or content item is ready, it will appear here with its evidence and exact approval boundary.</EmptyState> :
      <section className="panel decision-card"><SectionHeading kicker="Brand fact" title="A decision is ready" />
        <p>Review the linked evidence before recording your decision. This action is written to Paperclip and cannot be silently changed.</p>
        <form action={submitPaperclipDecision} className="stack-form">
          <input type="hidden" name="approval_id" value={approvalId} /><input type="hidden" name="expected_checksum" value={checksum} />
          <label>Your decision<select name="decision" required><option value="">Choose</option><option value="approve">Approve</option><option value="reject">Reject</option></select></label>
          <label>Decision note<textarea name="decision_note" required maxLength={1000} /></label>
          <ActionButton>Record decision in Paperclip</ActionButton>
        </form></section>}
  </>;
}
