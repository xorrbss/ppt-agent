import type { Theme } from "@/app/(presentation-generator)/services/api/types";

/** API failures surface as `ApiError extends Error`; anything else has no message. */
export const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : ''

// Fallback theme used before defaults are loaded from API (unified Theme type)
export const FALLBACK_THEME: Theme = {
  id: 'standard',
  name: 'Standard',
  description: 'Standard theme',
  user: 'system',
  logo: '',
  logo_url: '',
  data: {
    colors: {
      'primary': '#2563eb',
      'background': '#ffffff',
      'card': '#f8fafc',
      'stroke': '#e5e7eb',
      'primary_text': '#1e293b',
      'background_text': '#475569',
      'graph_0': '#2563eb',
      'graph_1': '#1d4ed8',
      'graph_2': '#1e40af',
      'graph_3': '#1e40af',
      'graph_4': '#1e40af',
      'graph_5': '#1e40af',
      'graph_6': '#1e40af',
      'graph_7': '#1e40af',
      'graph_8': '#1e40af',
      'graph_9': '#1e40af',
    },
    fonts: {
      textFont: { name: 'Inter', url: 'https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap' },
    },
  },
}
