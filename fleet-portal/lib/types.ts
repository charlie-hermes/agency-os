export type PortalContext = {
  hostname: string;
  origin: string;
  workos_subject: string;
  workos_organization_id: string;
  customer_account_id: string;
  client_brand_id: string;
  tenant_id: string;
  brand_id: string;
  client_role: "owner" | "approver" | "contributor" | "analyst" | "viewer";
  approval_scopes: string[];
  session_id: string;
  entitlement_version: number;
  correlation_id: string;
};

export type AuthorityIdentity = {
  workos_subject: string;
  workos_organization_id: string;
  hostname: string;
  origin: string;
  access_identity_verified: true;
  session_id: string;
  correlation_id: string;
};
