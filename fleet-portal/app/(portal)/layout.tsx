import { PortalShell } from "@/components/portal-shell";
import { requirePortalContext } from "@/lib/identity";

export const dynamic = "force-dynamic";

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const context = await requirePortalContext();
  return <PortalShell context={context}>{children}</PortalShell>;
}
