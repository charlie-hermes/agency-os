"use client";

import { useActionState } from "react";
import { issueInvitation, type InvitationActionState } from "@/app/actions";

const initialState: InvitationActionState = {};

export function InviteMember() {
  const [state, action, pending] = useActionState(issueInvitation, initialState);
  return <form action={action} className="stack-form">
    <label>Email address<input name="email" type="email" autoComplete="email" required /></label>
    <label>Fleet role<select name="client_role" defaultValue="viewer">
      <option value="owner">Owner</option>
      <option value="approver">Approver</option>
      <option value="contributor">Contributor</option>
      <option value="analyst">Analyst</option>
      <option value="viewer">Viewer</option>
    </select></label>
    <button className="primary-button" disabled={pending} data-disabled={pending || undefined}>
      {pending ? "Creating invitation…" : "Create secure invitation"}
    </button>
    {state.error ? <p role="alert" className="evidence-note">{state.error}</p> : null}
    {state.inviteUrl ? <div aria-live="polite"><p className="fine-print">Send this one-time link to the intended person. It expires after 72 hours.</p><input type="text" readOnly value={state.inviteUrl} aria-label="Secure invitation link" /></div> : null}
  </form>;
}
