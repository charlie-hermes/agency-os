import "server-only";

import { createRemoteJWKSet, jwtVerify } from "jose";

export async function verifyCloudflareAccess(
  token: string | null,
  surface: "portal" | "admin",
): Promise<void> {
  if (!token) throw new Error("Cloudflare Access identity is required.");
  const teamDomain = process.env.CLOUDFLARE_ACCESS_TEAM_DOMAIN;
  const audience = surface === "portal"
    ? process.env.CLOUDFLARE_PORTAL_ACCESS_AUDIENCE
    : process.env.CLOUDFLARE_ADMIN_ACCESS_AUDIENCE;
  if (!teamDomain || !audience || !/^[a-z0-9-]+\.cloudflareaccess\.com$/.test(teamDomain)) {
    throw new Error("Cloudflare Access is not configured.");
  }
  const issuer = `https://${teamDomain}`;
  const jwks = createRemoteJWKSet(new URL(`${issuer}/cdn-cgi/access/certs`));
  await jwtVerify(token, jwks, { issuer, audience });
}
