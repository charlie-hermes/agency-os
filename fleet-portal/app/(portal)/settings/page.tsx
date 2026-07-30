import { revokeMembership } from "@/app/actions";
import { InviteMember } from "@/components/access-management";
import { PageIntro, SectionHeading, Status } from "@/components/ui";
import { requirePortalContext } from "@/lib/identity";
import { getPortalProjection } from "@/lib/view-model";

export const metadata = { title: "Settings" };

export default async function SettingsPage() {
  const context = await requirePortalContext();
  const view = await getPortalProjection(context);
  const enabled = Object.entries(view.modules).filter(([, value]) => value).map(([key]) => key.replaceAll("_", " "));
  return <><PageIntro eyebrow="Settings" title="Access and service, made clear."
    description="Current package access and authorised users come from Fleet's protected authorities." />
    <section className="split-grid"><div className="panel"><SectionHeading kicker="Enabled modules" title="Fleet Brand OS" /><p>{enabled.join(", ") || "No modules enabled"}</p><Status tone={view.lifecycle_state === "active" ? "good" : "attention"}>{view.lifecycle_state}</Status></div>
      <div className="panel"><SectionHeading kicker="Your access" title={context.client_role} /><dl className="definition-list"><div><dt>Role</dt><dd>{context.client_role}</dd></div><div><dt>Organisation</dt><dd>{context.workos_organization_id}</dd></div><div><dt>Entitlement version</dt><dd>{context.entitlement_version}</dd></div></dl></div></section>
    <section className="panel"><SectionHeading kicker="People" title="Workspace access" />{view.memberships.map(member => <div className="person-row" key={member.membership_id}><span className="avatar">{member.client_role[0].toUpperCase()}</span><div><strong>{member.workos_subject}</strong><p>{member.client_role} · {member.approval_scopes.join(", ") || "no approval scopes"}</p></div>{context.client_role === "owner" && member.workos_subject !== context.workos_subject && member.state === "active" ? <form action={revokeMembership}><input type="hidden" name="membership_id" value={member.membership_id} /><button className="text-button">Revoke</button></form> : <Status tone={member.state === "active" ? "good" : "attention"}>{member.state}</Status>}</div>)}</section>
    {context.client_role === "owner" ? <section className="split-grid"><div className="panel"><SectionHeading kicker="Invite" title="Add an authorised person" /><InviteMember /></div><div className="panel"><SectionHeading kicker="Pending invitations" title="Invitation register" />{view.invitations.length ? view.invitations.map(invitation => <div key={invitation.invitation_id}><strong>{invitation.email}</strong><p>{invitation.client_role} · {invitation.state} · expires {new Date(invitation.expires_at).toLocaleString("en-GB")}</p></div>) : <p>No invitations have been issued.</p>}</div></section> : null}
  </>;
}
