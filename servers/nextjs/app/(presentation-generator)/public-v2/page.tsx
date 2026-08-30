import { createHash } from "node:crypto";

import { getServerAuthStatus } from "@/utils/serverAuth";

import PublicV2OperationsClient from "./PublicV2OperationsClient";

/**
 * The public-v2 surface is deliberately separate from the existing v1
 * generator routes. It is served inside the authenticated generator layout.
 */
export default async function PublicV2OperationsPage() {
  const auth = await getServerAuthStatus();
  const principalIdentity = auth.user_id ?? auth.username ?? "authenticated-session";
  const storageScope = createHash("sha256")
    .update(`public-v2:${principalIdentity}`)
    .digest("hex")
    .slice(0, 24);
  return <PublicV2OperationsClient storageScope={storageScope} />;
}
