import { PageIntro, SectionHeading, Status } from "@/components/ui";
import { confirmCandidate, queueSource } from "@/app/actions";
import { ActionButton } from "@/components/action-button";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export const metadata = { title: "Launch Room" };

export default async function LaunchPage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  const approvedSources = (view.source_counts.admitted ?? 0) + (view.source_counts.review_required ?? 0);
  const approvedClaims = view.brand_profile?.claims.length ?? view.brand_twin_claims.length;
  const openCandidates = view.candidates.filter(item => item.state === "client_review");
  const completion = openCandidates.length === 0 && approvedSources > 0 ? 100 : approvedSources > 0 ? 75 : 25;
  return <>
    <PageIntro eyebrow="Launch Room" title="Build the brand from evidence."
      description="Supply trusted material, follow its review status, and confirm every extracted fact before it can be approved." />
    <section className="launch-progress panel"><div><span className="metric-value">{completion}%</span><strong>Evidence journey</strong><p>Calculated from admitted sources and unresolved candidate reviews.</p></div>
      <div className="progress-track" aria-label="Launch completion" aria-valuenow={completion} role="progressbar"><i style={{ width: `${completion}%` }} /></div></section>
    <section className="split-grid"><div className="panel"><SectionHeading kicker="Secure source intake" title="Add trusted material" />
      <form action={queueSource} className="stack-form"><label>What will this source help Fleet understand?<textarea name="purpose" required maxLength={500} /></label>
        <label>Choose a file<input name="source" type="file" required accept=".pdf,.docx,.xlsx,.csv,.txt,.png,.jpg,.jpeg" /></label>
        <label className="check-row"><input name="consent" type="checkbox" value="yes" required /><span>I am authorised to share this material with Fleet for this brand.</span></label>
        <ActionButton>Send for safe review</ActionButton><p className="fine-print">Files are quarantined, scanned and extracted. Nothing is approved automatically.</p></form></div>
      <div className="panel"><SectionHeading kicker="Current evidence" title="What Fleet currently holds" />
        <dl className="definition-list"><div><dt>Sources</dt><dd>{approvedSources}</dd></div><div><dt>Approved claims</dt><dd>{approvedClaims}</dd></div><div><dt>Facts awaiting you</dt><dd>{openCandidates.length}</dd></div></dl>
        <Status tone={openCandidates.length ? "attention" : "good"}>{openCandidates.length ? "Review required" : "No client reviews waiting"}</Status></div></section>
    {openCandidates.length > 0 && <><SectionHeading kicker="Candidate facts" title="Confirm or correct what Fleet extracted" />
      <section className="claim-list">{openCandidates.map(candidate => <article key={candidate.candidate_id}><div><h3>{candidate.source_locator}</h3>
        <form action={confirmCandidate} className="stack-form"><input type="hidden" name="candidate_id" value={candidate.candidate_id} /><input type="hidden" name="expected_checksum" value={candidate.candidate_checksum} />
          <label>Reviewed statement<textarea name="statement" required maxLength={2000} defaultValue={candidate.statement} /></label><ActionButton>Confirm for Fleet review</ActionButton></form></div><Status>Client review</Status></article>)}</section></>}
  </>;
}
