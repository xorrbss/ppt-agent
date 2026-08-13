export type PresentationThemePayload = {
  id: string;
  name: string;
  description: string;
  user: string;
  logo: string | null;
  logo_url: string | null;
  company_name: string | null;
  data: Record<string, unknown>;
};

export type GenerationPreferences = {
  language?: string | null;
  tone?: string;
  verbosity?: string;
  instructions?: string | null;
  include_table_of_contents?: boolean;
  include_title_slide?: boolean;
  web_search?: boolean;
};
