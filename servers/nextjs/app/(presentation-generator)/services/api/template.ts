import { getApiUrl } from "@/utils/api";
import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";

export interface CloneTemplatePayload {
    id: string;
    name?: string;
    description?: string;
}

export interface CloneLayoutPayload {
    template_id: string;
    layout_id: string;
    layout_name?: string;
}

export interface GenerateTemplateLayoutPayload {
    template_id: string;
    prompt: string;
}

export interface GenerateTemplateLayoutResponse {
    layout: TemplateV2Layout;
    response: string;
}

export interface StructuredTemplateDetailsResponse {
    id: string;
    name: string;
    description?: string | null;
    layouts: unknown;
    assets?: unknown;
    is_default?: boolean;
    revision: number;
    updated_at?: string;
}

export interface UpdateStructuredTemplatePayload {
    layouts: unknown;
    expected_revision: number;
}

class TemplateService {

    static async getCustomTemplateSummaries() {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/all`),);
            return await ApiResponseHandler.handleResponse(response, "Failed to get custom template summaries");
        } catch (error) {
            console.error("Failed to get custom template summaries", error);
            throw error;
        }
    }

    static async getCustomTemplateDetails(templateId: string) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/${templateId}/layouts`),);
            return await ApiResponseHandler.handleResponse(response, "Failed to get custom template details");
        } catch (error) {
            console.error("Failed to get custom template details", error);
            throw error;
        }
    }

    static async deleteCustomTemplate(presentationId: string) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template-management/delete-templates/${presentationId}`), { method: "DELETE", headers: getHeader() });
            return await ApiResponseHandler.handleResponseWithResult(response, "Failed to delete custom template");
        } catch (error) {
            console.error("Failed to delete custom template", error);
            throw error;
        }
    }

    static async cloneCustomTemplate(payload: CloneTemplatePayload) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/clone`), {
                method: "POST",
                headers: getHeader(),
                body: JSON.stringify(payload),
            });
            return await ApiResponseHandler.handleResponse(response, "Failed to clone template");
        } catch (error) {
            console.error("Failed to clone template", error);
            throw error;
        }
    }

    static async cloneTemplateLayout(payload: CloneLayoutPayload) {
        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/slide-layout/clone`), {
                method: "POST",
                headers: getHeader(),
                body: JSON.stringify(payload),
            });
            return await ApiResponseHandler.handleResponse(response, "Failed to clone layout");
        } catch (error) {
            console.error("Failed to clone layout", error);
            throw error;
        }
    }

    static async getStructuredTemplateDetails(
        templateId: string,
    ): Promise<StructuredTemplateDetailsResponse> {
        try {
            const response = await fetch(
                getApiUrl(
                    `/api/v1/ppt/structured-templates/${encodeURIComponent(templateId)}`,
                ),
                { credentials: "include" },
            );
            return await ApiResponseHandler.handleResponse(
                response,
                "Failed to get structured template details",
            );
        } catch (error) {
            console.error("Failed to get structured template details", error);
            throw error;
        }
    }

    static async updateStructuredTemplateLayouts(
        templateId: string,
        payload: UpdateStructuredTemplatePayload,
    ): Promise<StructuredTemplateDetailsResponse> {
        try {
            const response = await fetch(
                getApiUrl(
                    `/api/v1/ppt/structured-templates/${encodeURIComponent(templateId)}`,
                ),
                {
                    method: "PATCH",
                    credentials: "include",
                    headers: getHeader(),
                    body: JSON.stringify(payload),
                },
            );
            return await ApiResponseHandler.handleResponse(
                response,
                "Failed to update structured template",
            );
        } catch (error) {
            console.error("Failed to update structured template", error);
            throw error;
        }
    }

    static async generateTemplateLayout(
        payload: GenerateTemplateLayoutPayload,
    ): Promise<GenerateTemplateLayoutResponse> {
        try {
            const response = await fetch(
                getApiUrl("/api/v1/ppt/template/layouts/generate"),
                {
                    method: "POST",
                    credentials: "include",
                    headers: getHeader(),
                    body: JSON.stringify(payload),
                },
            );
            return await ApiResponseHandler.handleResponse(
                response,
                "Failed to generate template layout",
            );
        } catch (error) {
            console.error("Failed to generate template layout", error);
            throw error;
        }
    }
}

export default TemplateService;
