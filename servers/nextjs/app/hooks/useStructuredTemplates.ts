"use client";

import { useCallback, useEffect, useState } from "react";

import { getHeader } from "@/app/(presentation-generator)/services/api/header";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { getApiUrl } from "@/utils/api";

export interface StructuredTemplateSummary {
  id: string;
  name: string;
  description: string | null;
  revision: number;
  is_default: boolean;
}

export const TEMPLATE_V2_SELECTION_PREFIX = "template-v2:";

export function makeTemplateV2SelectionId(
  templateId: string,
  revision: number
): string {
  return `${TEMPLATE_V2_SELECTION_PREFIX}${encodeURIComponent(
    templateId
  )}?revision=${revision}`;
}

export function parseTemplateV2SelectionId(
  selection: string
): { templateId: string; revision: number } | null {
  if (!selection.startsWith(TEMPLATE_V2_SELECTION_PREFIX)) return null;

  const value = selection.slice(TEMPLATE_V2_SELECTION_PREFIX.length);
  const separator = value.lastIndexOf("?revision=");
  if (separator <= 0) return null;

  const encodedTemplateId = value.slice(0, separator);
  const revision = Number(value.slice(separator + "?revision=".length));
  if (!Number.isSafeInteger(revision) || revision < 1) return null;

  try {
    const templateId = decodeURIComponent(encodedTemplateId).trim();
    return templateId ? { templateId, revision } : null;
  } catch {
    return null;
  }
}

export async function listStructuredTemplates(): Promise<
  StructuredTemplateSummary[]
> {
  const templates: StructuredTemplateSummary[] = [];
  const pageSize = 100;
  const maxPages = 100;

  for (let pageIndex = 0; pageIndex < maxPages; pageIndex += 1) {
    const offset = pageIndex * pageSize;
    const response = await fetch(
      getApiUrl(
        `/api/v1/ppt/structured-templates?offset=${offset}&limit=${pageSize}`
      ),
      {
        method: "GET",
        headers: getHeader(),
        cache: "no-cache",
      }
    );
    const page = (await ApiResponseHandler.handleResponse(
      response,
      "Failed to load structured templates"
    )) as StructuredTemplateSummary[];
    templates.push(...page);
    if (page.length < pageSize) return templates;
  }

  throw new Error("Structured template catalog exceeded the pagination limit");
}

export function useStructuredTemplateSummaries() {
  const [templates, setTemplates] = useState<StructuredTemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTemplates(await listStructuredTemplates());
    } catch (reason) {
      setTemplates([]);
      setError(
        reason instanceof Error
          ? reason
          : new Error("Failed to load structured templates")
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return { templates, loading, error, refetch };
}
