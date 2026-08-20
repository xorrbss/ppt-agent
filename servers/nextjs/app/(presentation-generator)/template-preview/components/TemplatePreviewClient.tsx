"use client";

import { useSearchParams } from "next/navigation";

import { StructuredTemplateLayoutEditor } from "./StructuredTemplateLayoutEditor";

export default function TemplatePreviewClient() {
  const searchParams = useSearchParams();
  const templateId = searchParams.get("templateV2Id") || searchParams.get("id");

  return templateId ? <StructuredTemplateLayoutEditor templateId={templateId} /> : null;
}
