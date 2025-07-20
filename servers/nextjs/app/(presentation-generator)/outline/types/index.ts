export interface LayoutGroup {
  id: string;
  name: string;
  description: string;
  ordered: boolean;
  isDefault?: boolean;
  slides?: any
}

export interface LoadingState {
  message: string;
  isLoading: boolean;
  showProgress: boolean;
  duration: number;
}

export interface StreamState {
  isStreaming: boolean;
  isLoading: boolean;
}

export const TABS = {
  OUTLINE: 'outline',
  LAYOUTS: 'layouts'
} as const;

export type TabType = typeof TABS[keyof typeof TABS]; 