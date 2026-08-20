import { getApiUrl } from "@/utils/api";
import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";

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

}

export default TemplateService;
