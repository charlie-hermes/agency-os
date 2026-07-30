import Link from "next/link";
import { signOut } from "@workos-inc/authkit-nextjs";
import type { PortalContext } from "@/lib/types";
import { ActionButton } from "@/components/action-button";

const navigation = [
  ["Overview", "/"], ["Launch Room", "/launch"], ["Decisions", "/decisions"],
  ["Brand", "/brand"], ["Content", "/content"], ["AI presence", "/ai-presence"],
  ["Settings", "/settings"],
] as const;

export function PortalShell({ context, children }: { context: PortalContext; children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#content">Skip to content</a>
      <aside className="sidebar">
        <Link className="brandmark" href="/" aria-label="Fleet home">
          <span className="brandmark-signal" aria-hidden="true"><i /><i /><i /></span>
          <span>FLEET</span>
        </Link>
        <div className="tenant-card">
          <span className="eyebrow">Workspace</span>
          <strong>Fleet</strong>
          <span className="tenant-state"><i /> Private authorised workspace</span>
        </div>
        <nav aria-label="Primary">
          {navigation.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
        </nav>
        <div className="sidebar-footer">
          <span className="eyebrow">Signed in as</span>
          <span>{context.client_role}</span>
          <form action={async () => { "use server"; await signOut(); }}>
            <ActionButton className="text-button">Sign out</ActionButton>
          </form>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div><span className="eyebrow">Fleet Brand OS</span><span className="topbar-title">One view of what matters now</span></div>
          <div className="topbar-actions"><span className="private-pill">Private</span><span className="avatar" aria-label="Fleet owner">F</span></div>
        </header>
        <div id="content" className="page-content">{children}</div>
      </main>
    </div>
  );
}
