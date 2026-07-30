import { acceptInvitation } from "@/app/actions";

export const metadata = { title: "Accept invitation" };
export const dynamic = "force-dynamic";

export default async function InvitationPage({
  params,
  searchParams,
}: {
  params: Promise<{ invitationId: string }>;
  searchParams: Promise<{ token?: string }>;
}) {
  const { invitationId } = await params;
  const { token = "" } = await searchParams;
  return <main className="error-page">
    <span className="eyebrow">Secure workspace invitation</span>
    <h1>Join Fleet.</h1>
    <p>Continue only if this invitation was sent to your signed-in WorkOS email address. Fleet checks the organisation, email, hostname and one-time token before granting access.</p>
    <form action={acceptInvitation}>
      <input type="hidden" name="invitation_id" value={invitationId} />
      <input type="hidden" name="invitation_token" value={token} />
      <button className="primary-button">Accept invitation</button>
    </form>
  </main>;
}
