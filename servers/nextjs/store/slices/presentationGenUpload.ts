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

/** UI selection only.  This is not the legacy Presentation.generation_mode codec. */
export type SourceLockedUiGenerationMode = "adaptive" | "source_locked";

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

  /** Phase A source-locked admission choice, scoped to the upload composer. */
  sourceLockedGenerationMode: SourceLockedUiGenerationMode;
  /** The mode becomes immutable at the first accepted attachment and only unlocks when all are removed. */
  sourceLockedGenerationModeLatched: boolean;
}

const initialState: PresentationGenUploadState = {
  config: null,
  files: [],
  selectedTheme: null, // null = no preset; deck keeps the default (theme picked later in the editor)
  authoredVisionQa: false,
  authoredStyle: "default",
  companyStandard: null,
  sourceLockedGenerationMode: "adaptive",
  sourceLockedGenerationModeLatched: false,
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
        sourceLockedGenerationMode,
      } = action.payload;
      if (config !== undefined) state.config = config;
      if (files !== undefined) state.files = files;
      if (selectedTheme !== undefined) state.selectedTheme = selectedTheme;
      if (authoredVisionQa !== undefined) state.authoredVisionQa = authoredVisionQa;
      if (authoredStyle !== undefined) state.authoredStyle = authoredStyle;
      if (companyStandard !== undefined) {
        state.companyStandard = companyStandard;
      }
      if (
        sourceLockedGenerationMode !== undefined &&
        !state.sourceLockedGenerationModeLatched
      ) {
        state.sourceLockedGenerationMode = sourceLockedGenerationMode;
      }
    },

    setSourceLockedGenerationMode: (
      state,
      action: PayloadAction<SourceLockedUiGenerationMode>,
    ) => {
      if (!state.sourceLockedGenerationModeLatched) {
        state.sourceLockedGenerationMode = action.payload;
      }
    },

    syncSourceLockedAttachmentLatch: (state, action: PayloadAction<number>) => {
      state.sourceLockedGenerationModeLatched = action.payload > 0;
    },

  },
});

export const {
  setPptGenUploadState,
  setSourceLockedGenerationMode,
  syncSourceLockedAttachmentLatch,
} =
  presentationGenUploadSlice.actions;
export default presentationGenUploadSlice.reducer;
