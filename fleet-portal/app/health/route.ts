import { NextResponse } from "next/server";
import { authorityCall } from "@/lib/authority";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const authority = await authorityCall<{ status: string; authority: string }>({ operation: "health" });
    if (authority.status !== "pass") throw new Error("authority health failed");
    return NextResponse.json(
      { status: "pass", service: "fleet-portal-web", authority: authority.authority },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { status: "fail", service: "fleet-portal-web" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
