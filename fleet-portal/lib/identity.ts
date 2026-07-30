import "server-only";

import { randomUUID } from "node:crypto";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { withAuth } from "@workos-inc/authkit-nextjs";
import { resolvePortalContext } from "@/lib/authority";
import { verifyCloudflareAccess } from "@/lib/edge-identity";
import type { AuthorityIdentity, PortalContext } from "@/lib/types";

function exactHost(value: string | null, fixture = false): string {
  if (fixture && (value === "127.0.0.1:3190" || value === "localhost:3190")) {
    return "fleet.madebyfleet.com";
  }
  if (!value || value.includes(":") || value.includes("/") || !/^[a-z0-9-]+\.madebyfleet\.com$/.test(value)) {
    notFound();
  }
  return value;
}

export async function requirePortalContext(options?: { mutation?: boolean }): Promise<PortalContext> {
  const requestHeaders = await headers();
  const mode = process.env.FLEET_PORTAL_IDENTITY_MODE ?? "workos";
  const fixture = mode === "fixture" && process.env.NODE_ENV !== "production" &&
    process.env.FLEET_PORTAL_FIXTURE_ACK === "local-test-only";
  const hostname = exactHost(requestHeaders.get("host"), fixture);
  const clientHost = process.env.FLEET_PORTAL_CLIENT_HOST ?? "fleet.madebyfleet.com";
  if (hostname !== clientHost) notFound();
  if (mode === "fixture") {
    if (process.env.NODE_ENV === "production" || process.env.FLEET_PORTAL_FIXTURE_ACK !== "local-test-only") {
      throw new Error("Fixture identity is forbidden outside an acknowledged local test.");
    }
    return {
      hostname, origin: `https://${hostname}`, workos_subject: "user_fleet_fixture",
      workos_organization_id: "org_fleet_g26_acceptance",
      customer_account_id: "account_fleet", client_brand_id: "client_brand_fleet",
      tenant_id: "tenant_fleet", brand_id: "brand_fleet", client_role: "owner",
      approval_scopes: ["brand_fact", "claim", "content", "publication", "access_change"],
      session_id: "session_local_fixture", entitlement_version: 1,
      correlation_id: randomUUID(),
    };
  }
  const { user, organizationId, sessionId } = await withAuth({ ensureSignedIn: true });
  const expectedOrganization = process.env.FLEET_WORKOS_ORGANIZATION_ID;
  if (!organizationId || !expectedOrganization || organizationId !== expectedOrganization) {
    throw new Error("Your WorkOS organisation is not admitted to this Fleet tenant.");
  }
  await verifyCloudflareAccess(requestHeaders.get("cf-access-jwt-assertion"), "portal");
  const actualOrigin = requestHeaders.get("origin");
  const origin = options?.mutation ? actualOrigin : `https://${hostname}`;
  if (origin !== `https://${hostname}`) throw new Error("The request origin is not admitted.");
  const identity: AuthorityIdentity = {
    workos_subject: user.id,
    workos_organization_id: organizationId,
    hostname,
    origin,
    access_identity_verified: true,
    session_id: `workos:${sessionId}`,
    correlation_id: randomUUID(),
  };
  return resolvePortalContext(identity);
}

export async function requireFleetAdministrator(): Promise<{ userId: string; hostname: string }> {
  const requestHeaders = await headers();
  const hostname = exactHost(requestHeaders.get("host"));
  const adminHost = process.env.FLEET_PORTAL_ADMIN_HOST ?? "admin.madebyfleet.com";
  if (hostname !== adminHost) notFound();
  const { user, organizationId } = await withAuth({ ensureSignedIn: true });
  await verifyCloudflareAccess(requestHeaders.get("cf-access-jwt-assertion"), "admin");
  if (organizationId !== process.env.FLEET_WORKOS_ORGANIZATION_ID) {
    throw new Error("The Fleet administration organisation is not admitted.");
  }
  const administrators = new Set(
    (process.env.FLEET_PORTAL_ADMIN_USER_IDS ?? "").split(",").filter(Boolean),
  );
  if (!administrators.has(user.id)) throw new Error("Fleet administrator access is required.");
  return { userId: user.id, hostname };
}

export async function requireInvitationIdentity(): Promise<{
  userId: string; email: string; organizationId: string; hostname: string; origin: string;
}> {
  const requestHeaders = await headers();
  const hostname = exactHost(requestHeaders.get("host"));
  const clientHost = process.env.FLEET_PORTAL_CLIENT_HOST ?? "fleet.madebyfleet.com";
  if (hostname !== clientHost) notFound();
  const { user, organizationId } = await withAuth({ ensureSignedIn: true });
  if (!organizationId || organizationId !== process.env.FLEET_WORKOS_ORGANIZATION_ID) {
    throw new Error("Your WorkOS organisation is not admitted to this invitation.");
  }
  await verifyCloudflareAccess(requestHeaders.get("cf-access-jwt-assertion"), "portal");
  const origin = requestHeaders.get("origin");
  if (origin !== `https://${hostname}`) throw new Error("The invitation origin is not admitted.");
  if (!user.email) throw new Error("Your WorkOS identity has no verified invitation email.");
  return { userId: user.id, email: user.email, organizationId, hostname, origin };
}
