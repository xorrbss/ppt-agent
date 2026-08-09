export type SettingsMode = "nanobanana" | "presenton";

export type SettingsSection =
  | "text-provider"
  | "image-provider"
  | "privacy"
  | "admin"
  | "session";

// Button state interface
export interface ButtonState {
  isLoading: boolean;
  isDisabled: boolean;
  text: string;
  showProgress: boolean;
  progressPercentage?: number;
  status?: string;
}

export interface DownloadingModel {
  name: string;
  size: number | null;
  downloaded: number | null;
  status: string;
  done: boolean;
}
