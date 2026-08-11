import type { ThemeColors } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/types";

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
    id: string;
    file_url: string;
}

export interface ThemeFont {
    name: string;
    url: string;
}

export interface ThemeFonts {
    textFont: ThemeFont;
    /** v2 themes may split heading/body; v1 themes carry only `textFont`. */
    headingFont?: ThemeFont;
    bodyFont?: ThemeFont;
}

/**
 * Colours and fonts are read directly by the theme UI and the renderers. The
 * optional tone & manner tokens (density/typography/shape/elevation/motif) are
 * consumed defensively in `presentationThemeTokens.deriveThemeTokens`, so they
 * stay untyped here instead of being pinned to a guessed shape.
 */
export interface ThemeData {
    colors: ThemeColors;
    fonts: ThemeFonts;
    [key: string]: unknown;
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
