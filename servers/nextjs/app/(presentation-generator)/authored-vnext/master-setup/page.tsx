import { notFound } from "next/navigation";

import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";

import MasterSetupGate from "./MasterSetupGate";

interface MasterSetupPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function AuthoredVnextMasterSetupPage({ searchParams }: MasterSetupPageProps) {
  if (!isAuthoredVnextMasterEnabled()) notFound();
  const params = await searchParams;
  const requestedContinue = params.continue ?? params.returnTo;
  const continueUrl = Array.isArray(requestedContinue) ? requestedContinue[0] : requestedContinue;
  return <MasterSetupGate continueUrl={continueUrl} />;
}
