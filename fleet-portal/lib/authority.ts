import "server-only";

import net from "node:net";
import type { AuthorityIdentity, PortalContext } from "@/lib/types";

type AuthorityResponse<T> = { ok: true; result: T } | { ok: false; error: string };

export async function authorityCall<T>(request: object): Promise<T> {
  const socketPath = process.env.FLEET_PORTAL_AUTHORITY_SOCKET;
  if (!socketPath?.startsWith("/run/agency-os/")) {
    throw new Error("The Fleet authority socket is not configured safely.");
  }
  const encoded = `${JSON.stringify(request)}\n`;
  if (Buffer.byteLength(encoded) > 64 * 1024) {
    throw new Error("Authority request exceeds the admitted size.");
  }
  return new Promise<T>((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let response = "";
    const timer = setTimeout(() => socket.destroy(new Error("Authority timed out.")), 2_000);
    socket.setEncoding("utf8");
    socket.on("connect", () => socket.end(encoded));
    socket.on("data", (chunk: string) => {
      response += chunk;
      if (response.length > 128 * 1024) socket.destroy(new Error("Authority response is too large."));
    });
    socket.on("error", reject);
    socket.on("close", () => {
      clearTimeout(timer);
      try {
        const value = JSON.parse(response) as AuthorityResponse<T>;
        if (!value.ok) throw new Error("The authority denied this request.");
        resolve(value.result);
      } catch (error) {
        reject(error);
      }
    });
  });
}

export function resolvePortalContext(identity: AuthorityIdentity): Promise<PortalContext> {
  return authorityCall<PortalContext>({ operation: "resolve_context", ...identity });
}
