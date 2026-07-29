"use server";

import { createHash, randomUUID } from "node:crypto";
import { open, rename, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { checkRecentAuth, getSignInUrl } from "@workos-inc/authkit-nextjs";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { authorityCall } from "@/lib/authority";
import { requirePortalContext } from "@/lib/identity";

const ALLOWED = new Set(["pdf", "docx", "xlsx", "csv", "txt", "png", "jpg", "jpeg"]);

async function requireRecentIdentity() {
  if (process.env.FLEET_PORTAL_IDENTITY_MODE !== "fixture") {
    const { isStale } = await checkRecentAuth({ maxAge: 600 });
    if (isStale) redirect(await getSignInUrl({ maxAge: 600 }));
  }
  return requirePortalContext({ mutation: true });
}

export async function queueSource(formData: FormData) {
  const context = await requireRecentIdentity();
  if (!["owner", "approver", "contributor"].includes(context.client_role)) {
    throw new Error("Your role cannot supply sources.");
  }
  const file = formData.get("source");
  const purpose = String(formData.get("purpose") ?? "").trim();
  const consent = formData.get("consent") === "yes";
  if (!(file instanceof File) || !purpose || !consent) throw new Error("A file, purpose and consent are required.");
  if (file.size < 1 || file.size > 50 * 1024 * 1024) throw new Error("The file must be between 1 byte and 50 MiB.");
  const safeName = basename(file.name);
  const extension = safeName.includes(".") ? safeName.split(".").at(-1)?.toLowerCase() : undefined;
  if (safeName !== file.name || !extension || !ALLOWED.has(extension)) throw new Error("This file type is not admitted.");
  const spool = process.env.FLEET_INGEST_SPOOL;
  if (!spool?.startsWith("/var/spool/agency-os/fleet-ingest/incoming")) {
    throw new Error("The source intake spool is not configured safely.");
  }
  const sourceId = `source_${randomUUID()}`;
  const finalPath = join(spool, `${sourceId}.${extension}`);
  const temporaryPath = `${finalPath}.part`;
  const handle = await open(temporaryPath, "wx", 0o640);
  try {
    await handle.writeFile(Buffer.from(await file.arrayBuffer()));
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temporaryPath, finalPath);
  await writeFile(
    `${finalPath}.json`,
    JSON.stringify({
      schema_version: "1.0", source_id: sourceId, original_filename: safeName,
      declared_content_type: file.type || "application/octet-stream",
      purpose, consent_basis: "authenticated_owner_confirmation",
      customer_account_id: context.customer_account_id,
      client_brand_id: context.client_brand_id, tenant_id: context.tenant_id,
      brand_id: context.brand_id, submitted_by: context.workos_subject,
      correlation_id: context.correlation_id,
    }),
    { encoding: "utf8", mode: 0o640, flag: "wx" },
  );
  revalidatePath("/launch");
}

export async function submitPaperclipDecision(formData: FormData) {
  const context = await requireRecentIdentity();
  const approvalId = String(formData.get("approval_id") ?? "");
  const expectedChecksum = String(formData.get("expected_checksum") ?? "");
  const decision = String(formData.get("decision") ?? "");
  const note = String(formData.get("decision_note") ?? "").trim();
  if (!/^[0-9a-f-]{36}$/.test(approvalId) || !/^sha256:[0-9a-f]{64}$/.test(expectedChecksum)) {
    throw new Error("The exact approval binding is invalid.");
  }
  if (!new Set(["approve", "reject"]).has(decision) || note.length < 3 || note.length > 1000) {
    throw new Error("A valid decision and short decision note are required.");
  }
  const idempotencyKey = `${context.session_id}:${approvalId}:${expectedChecksum}:${decision}`;
  const commandId = `command_${createHash("sha256").update(idempotencyKey).digest("hex").slice(0, 32)}`;
  await authorityCall({
    operation: "submit_command",
    workos_subject: context.workos_subject,
    workos_organization_id: context.workos_organization_id,
    hostname: context.hostname, origin: context.origin,
    access_identity_verified: true, session_id: context.session_id,
    correlation_id: context.correlation_id,
    command: {
      command_id: commandId,
      idempotency_key: idempotencyKey,
      command_type: "paperclip_approval_decision", target_id: approvalId,
      expected_checksum: expectedChecksum, approval_scope: "brand_fact",
      payload: { decision, decision_note: note },
    },
  });
  revalidatePath("/decisions");
}
