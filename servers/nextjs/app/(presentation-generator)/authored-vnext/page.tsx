import { notFound } from "next/navigation";

import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";

import AuthoredVnextFlowClient from "./AuthoredVnextFlowClient";

export default function AuthoredVnextPage() {
  if (!isAuthoredVnextMasterEnabled()) notFound();
  return <AuthoredVnextFlowClient />;
}
