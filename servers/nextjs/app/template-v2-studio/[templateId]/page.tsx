import { notFound } from "next/navigation";

import { isTemplateV2StudioEnabled } from "@/lib/template-v2-studio";
import TemplateV2StudioLoader from "./TemplateV2StudioLoader";

interface TemplateV2StudioPageProps {
  params: Promise<{ templateId: string }>;
}

export default async function TemplateV2StudioPage({
  params,
}: TemplateV2StudioPageProps) {
  if (!isTemplateV2StudioEnabled()) {
    notFound();
  }

  const { templateId } = await params;
  return <TemplateV2StudioLoader templateId={templateId} />;
}
