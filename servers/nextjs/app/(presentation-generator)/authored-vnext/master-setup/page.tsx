import { notFound } from "next/navigation";

import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";

import MasterSetupGate from "./MasterSetupGate";

export default function AuthoredVnextMasterSetupPage() {
  if (!isAuthoredVnextMasterEnabled()) notFound();
  return <MasterSetupGate />;
}
