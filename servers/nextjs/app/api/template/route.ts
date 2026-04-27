import { NextResponse } from "next/server";
import { validate as uuidValidate } from "uuid";

import { getSchemaByTemplateId, getSettingsByTemplateId } from "@/app/presentation-templates";
import { compileTemplateSchema } from "@/lib/compile-template-schema";

type CustomTemplateLayoutsResponse = {
  layouts: Array<{
    layout_code: string;
    layout_id: string;
    layout_name: string;
    template: string;
  }>;
  template?: {
    description?: string | null;
    id: string;
    name?: string | null;
  } | null;
};

function getFastApiBaseUrl(): string {
  return (
    process.env.FAST_API_INTERNAL_URL?.trim() ||
    process.env.NEXT_PUBLIC_FAST_API?.trim() ||
    "http://127.0.0.1:8000"
  );
}

function isCustomTemplateId(groupName: string): boolean {
  return groupName.startsWith("custom-") || uuidValidate(groupName);
}

async function getCustomTemplateResponse(groupName: string) {
  const templateId = groupName.startsWith("custom-")
    ? groupName.slice("custom-".length)
    : groupName;
  const response = await fetch(
    `${getFastApiBaseUrl()}/api/v1/ppt/template/${templateId}/layouts`,
    {
      cache: "no-store",
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch template data. HTTP ${response.status}`);
  }

  const data = (await response.json()) as CustomTemplateLayoutsResponse;
  return {
    name: data.template?.name || groupName,
    ordered: false,
    slides: data.layouts
      .map((layout) => {
        const compiledLayout = compileTemplateSchema(layout.layout_code);
        if (!compiledLayout) {
          return null;
        }

        return {
          description: compiledLayout.layoutDescription,
          id: `custom-${templateId}:${compiledLayout.layoutId}`,
          json_schema: compiledLayout.schemaJSON,
          name: compiledLayout.layoutName,
        };
      })
      .filter(
        (
          layout
        ): layout is {
          description: string;
          id: string;
          json_schema: unknown;
          name: string;
        } => layout !== null
      ),
  };
}

function getBuiltInTemplateResponse(groupName: string) {
  const settings = getSettingsByTemplateId(groupName);

  return {
    name: groupName,
    ordered: settings?.ordered ?? false,
    slides: getSchemaByTemplateId(groupName),
  };
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const groupName = searchParams.get("group");

  if (!groupName) {
    return NextResponse.json({ error: "Missing group name" }, { status: 400 });
  }

  try {
    const response = isCustomTemplateId(groupName)
      ? await getCustomTemplateResponse(groupName)
      : getBuiltInTemplateResponse(groupName);

    return NextResponse.json(response);
  } catch (error) {
    console.error("[api/template]", error);
    return NextResponse.json(
      { error: "Failed to fetch template data" },
      { status: 500 }
    );
  }
}
