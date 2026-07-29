import { PageIntro, SectionHeading, Status } from "@/components/ui";
import { portalSnapshot } from "@/lib/view-model";
import { queueSource } from "@/app/actions";
import { ActionButton } from "@/components/action-button";

export const metadata = { title: "Launch Room" };

export default function LaunchPage() {
  return <>
    <PageIntro eyebrow="Launch Room" title="Build the brand from evidence."
      description="Supply trusted material, understand why Fleet needs it, and review every fact before it becomes active." />
    <section className="launch-progress panel"><div><span className="metric-value">{portalSnapshot.launch.completion}%</span><strong>Fleet foundation ready</strong><p>The internal pilot has passed its existing product gates.</p></div>
      <div className="progress-track" aria-label="Launch completion" aria-valuenow={100} role="progressbar"><i style={{ width: "100%" }} /></div></section>
    <section className="split-grid">
      <div className="panel"><SectionHeading kicker="Secure source intake" title="Add trusted material" />
        <form action={queueSource} className="stack-form">
          <label>What will this source help Fleet understand?<textarea name="purpose" required maxLength={500} placeholder="For example: approved product facts and claim limitations" /></label>
          <label>Choose a file<input name="source" type="file" required accept=".pdf,.docx,.xlsx,.csv,.txt,.png,.jpg,.jpeg" /></label>
          <label className="check-row"><input name="consent" type="checkbox" value="yes" required /><span>I am authorised to share this material with Fleet for this brand.</span></label>
          <ActionButton>Send for safe review</ActionButton>
          <p className="fine-print">Files are quarantined and scanned. Extracted statements remain candidates until an authorised person confirms them.</p>
        </form>
      </div>
      <div className="panel"><SectionHeading kicker="Approved foundation" title="What Fleet currently knows" />
        <dl className="definition-list"><div><dt>Approved sources</dt><dd>{portalSnapshot.launch.sources}</dd></div><div><dt>Evidence-bound claims</dt><dd>{portalSnapshot.launch.approvedFacts}</dd></div><div><dt>Open questions</dt><dd>{portalSnapshot.launch.openQuestions}</dd></div></dl>
        <Status tone="good">Every current claim traces to an admitted source</Status>
      </div>
    </section>
  </>;
}
