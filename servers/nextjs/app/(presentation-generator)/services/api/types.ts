import type { ThemeColors } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/types";

export interface ThemeFont {
    name: string;
    url: string;
}

export interface ThemeFonts {
    /** Always present; v1 themes carry only this one. */
    textFont: ThemeFont;
    /** v2 heading/body split; absent themes fall back to textFont. */
    headingFont?: ThemeFont;
    bodyFont?: ThemeFont;
}

/**
 * Persisted theme payload. `colors` and `fonts` are always present; the tone &
 * manner keys (density / typography / shape / elevation / motif) are optional,
 * additive extensions read only by the adaptive renderer, so the index
 * signature keeps both v1 themes and newer keys assignable.
 */
export interface ThemeData {
    colors: ThemeColors;
    fonts: ThemeFonts;
    [key: string]: unknown;
}

export interface ChartAssignmentResponse {
    title_with_charts: {
        title: string;
        graph_id: string | null;
    }[];
    charts: {
        id: string;
        name: string;
        type: string;
        presentation: string;
        postfix: string | null;
        data: {
            categories: string[];
            series: {
                name: string;
                data: number[];
            }[];
        };
    }[];
}

export interface DeplotResponse {
    presentation_id: string;
    charts: ChartAssignmentResponse;
}

export interface ImageAssetResponse {
    message: string;
    path: string;
    id: string;
    file_url?: string;
}



export interface DefaultTheme {
    id: string;
    name: string;
    description: string;
    data: ThemeData;
}


export interface Theme {
    id: string;
    name: string;
    description: string;
    user: string;
    logo: string; // image id
    logo_url?: string; // preview url
    company_name?: string;
    data: ThemeData;
}
export interface ThemeParams {
    id?: string;
    name: string;
    description: string;
    logo: string | null; // image id
    logo_url?: string | null; // preview url
    data: ThemeData;
    company_name?: string | null;
}


export interface DefaultTheme {
    id: string;
    name: string;
    description: string;
    data: ThemeData;
}