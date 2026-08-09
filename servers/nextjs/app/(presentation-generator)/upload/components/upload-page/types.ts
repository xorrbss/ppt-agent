// Types for loading state
export interface LoadingState {
  isLoading: boolean;
  message: string;
  duration?: number;
  showProgress?: boolean;
  progress?: number;
  progressLabel?: string;
  extra_info?: string;
}
