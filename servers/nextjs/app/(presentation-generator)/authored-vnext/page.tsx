import { notFound } from "next/navigation";

import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";

import AuthoredVnextFlowClient from "./AuthoredVnextFlowClient";

export default function AuthoredVnextPage() {
  const internalCompatibilityUi =
    process.env.AUTHORED_VNEXT_INTERNAL_COMPATIBILITY_UI === "true";
  if (!internalCompatibilityUi || !isAuthoredVnextMasterEnabled()) notFound();
  return <AuthoredVnextFlowClient />;
}
