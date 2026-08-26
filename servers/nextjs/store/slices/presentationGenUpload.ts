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

/** Immutable analysis pin retained during same-navigation handoff. */
export interface SourceContentAdmissionSelection {
  manifestPin: {
    manifestId: string;
    digest: `sha256:${string}`;
  };
  referenceSnapshotId: string;
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

  /**
   * Preserve the source payload while applying the selected authored template.
   * This is deliberately distinct from the legacy byte-copy `source_locked`
   * restoration feature and is never latched by attachment order.
   */
  sourceContentMode: boolean;
  sourceContentAdmission: SourceContentAdmissionSelection | null;
}

const initialState: PresentationGenUploadState = {
  config: null,
  files: [],
  selectedTheme: null, // null = no preset; deck keeps the default (theme picked later in the editor)
  authoredVisionQa: false,
  authoredStyle: "default",
  companyStandard: null,
  sourceContentMode: false,
  sourceContentAdmission: null,
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
        sourceContentMode,
        sourceContentAdmission,
      } = action.payload;
      if (config !== undefined) state.config = config;
      if (files !== undefined) state.files = files;
      if (selectedTheme !== undefined) state.selectedTheme = selectedTheme;
      if (authoredVisionQa !== undefined) state.authoredVisionQa = authoredVisionQa;
      if (authoredStyle !== undefined) state.authoredStyle = authoredStyle;
      if (companyStandard !== undefined) {
        state.companyStandard = companyStandard;
      }
      if (sourceContentMode !== undefined) state.sourceContentMode = sourceContentMode;
      if (sourceContentAdmission !== undefined) {
        state.sourceContentAdmission = sourceContentAdmission;
      }
    },

    setSourceContentMode: (
      state,
      action: PayloadAction<boolean>,
    ) => {
      state.sourceContentMode = action.payload;
    },
  },
});

export const {
  setPptGenUploadState,
  setSourceContentMode,
} =
  presentationGenUploadSlice.actions;
export default presentationGenUploadSlice.reducer;
