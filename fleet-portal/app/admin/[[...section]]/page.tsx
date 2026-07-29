import Link from "next/link";
import { PageIntro, SectionHeading, Status } from "@/components/ui";
import { requireFleetAdministrator } from "@/lib/identity";

export const dynamic = "force-dynamic";
const sections = ["accounts", "brands", "tenants", "provisioning", "users", "entitlements", "support", "health", "audit"];

export default async function AdminPage({ params }: { params: Promise<{ section?: string[] }> }) {
  await requireFleetAdministrator();
  const value = await params;
  const section = value.section?.[0] ?? "accounts";
  if (!sections.includes(section)) throw new Error("Unknown Fleet administration section.");
  return <div className="admin-shell"><aside><Link className="brandmark" href="/admin"><span>FLEET / ADMIN</span></Link><nav>{sections.map(item => <Link href={`/admin/${item}`} key={item}>{item.replaceAll("-", " ")}</Link>)}</nav><a href="https://fleet.madebyfleet.com/">Open client view →</a></aside><main className="admin-main"><PageIntro eyebrow="Fleet administration" title={section[0].toUpperCase() + section.slice(1)} description="Fleet-only operational control. Client identities cannot enter this surface." />
    <section className="metric-grid compact"><article className="metric green"><span className="metric-value">1</span><strong>Production tenant</strong><p>Fleet DMA only</p></article><article className="metric"><span className="metric-value">0</span><strong>External clients</strong><p>G2.7 remains separately gated</p></article><article className="metric"><span className="metric-value">4</span><strong>Protected services</strong><p>Web, authority, command and ingest workers</p></article></section>
    <section className="panel"><SectionHeading kicker="Current state" title={`${section} controls are ready`} /><p>The G2.6 implementation exposes the bounded Fleet DMA control surface. External tenant creation, billing, portfolio views and public signup remain disabled.</p><Status tone="good">Exact tenant and Fleet-role boundary enforced</Status></section>
  </main></div>;
}
