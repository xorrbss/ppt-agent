import { PresentationConfig } from "@/app/(presentation-generator)/upload/type";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";

/** Immutable published company-standard revision selected for a new deck. */
export interface CompanyStandardSelection {
  masterId: string;
  revision: number;
  digest: `sha256:${string}`;
  name: string;
  hasLogo: boolean;
  primaryFont: string;
  layoutCount: number;
}

interface PresentationGenUploadState {
  config: PresentationConfig | null;

  files: any;

  /** Selected theme-preset id (a DEFAULT_THEMES id) applied to the deck after generation; null = none. */
  selectedTheme: string | null;

  /** Authored mode only: opt into the vision-QA self-correction pass (higher quality,
   * slower — it adds a second authoring round). Off by default for speed. */
  authoredVisionQa: boolean;

  /** Authored mode only: selected server-authored visual style id. */
  authoredStyle: string;

  /**
   * Company furniture/brand pin. This is deliberately independent from
   * `authoredStyle`: the server resolves both into one immutable composition
   * before it admits the generation job.
   */
  companyStandard: CompanyStandardSelection | null;
}

const initialState: PresentationGenUploadState = {
  config: null,
  files: [],
  selectedTheme: null, // null = no preset; deck keeps the default (theme picked later in the editor)
  authoredVisionQa: false,
  authoredStyle: "default",
  companyStandard: null,
};

export const presentationGenUploadSlice = createSlice({
  name: "pptGenUpload",
  initialState,
  reducers: {
    setPptGenUploadState: (
      state,
      action: PayloadAction<Partial<PresentationGenUploadState>>
    ) => {
      // Partial merge: callers can update a single authored input without
      // clearing the independently selected company standard.
      const {
        config,
        files,
        selectedTheme,
        authoredVisionQa,
        authoredStyle,
        companyStandard,
      } = action.payload;
      if (config !== undefined) state.config = config;
      if (files !== undefined) state.files = files;
      if (selectedTheme !== undefined) state.selectedTheme = selectedTheme;
      if (authoredVisionQa !== undefined) state.authoredVisionQa = authoredVisionQa;
      if (authoredStyle !== undefined) state.authoredStyle = authoredStyle;
      if (companyStandard !== undefined) {
        state.companyStandard = companyStandard;
      }
    },

  },
});

export const { setPptGenUploadState, } =
  presentationGenUploadSlice.actions;
export default presentationGenUploadSlice.reducer;
