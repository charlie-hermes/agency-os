import { randomBytes } from "node:crypto";
import { authkit, handleAuthkitHeaders } from "@workos-inc/authkit-nextjs";
import { NextResponse, type NextRequest } from "next/server";

export async function proxy(request: NextRequest) {
  const nonce = randomBytes(16).toString("base64");
  const fixture = process.env.FLEET_PORTAL_IDENTITY_MODE === "fixture" &&
    process.env.NODE_ENV !== "production" &&
    process.env.FLEET_PORTAL_FIXTURE_ACK === "local-test-only";
  const auth = fixture ? null : await authkit(request);
  const headers = auth?.headers ?? new Headers(request.headers);
  headers.set("x-nonce", nonce);
  const protectedPath = !request.nextUrl.pathname.startsWith("/auth/") &&
    request.nextUrl.pathname !== "/sign-in" && request.nextUrl.pathname !== "/health";
  const response = fixture
    ? NextResponse.next({ request: { headers } })
    : protectedPath && !auth?.session.user && auth?.authorizationUrl
      ? handleAuthkitHeaders(request, headers, { redirect: auth.authorizationUrl })
      : handleAuthkitHeaders(request, headers);
  const scriptSource = fixture
    ? `script-src 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval'`
    : `script-src 'nonce-${nonce}' 'strict-dynamic'`;
  response.headers.set(
    "Content-Security-Policy",
    [
      "default-src 'none'", scriptSource,
      "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
      "font-src 'self'", "connect-src 'self'", "frame-ancestors 'none'",
      "base-uri 'none'", "form-action 'self'", "object-src 'none'",
      "upgrade-insecure-requests",
    ].join("; "),
  );
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()");
  response.headers.set("Cross-Origin-Opener-Policy", "same-origin");
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
