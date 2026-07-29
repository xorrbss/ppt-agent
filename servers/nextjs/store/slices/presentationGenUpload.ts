import { PresentationConfig } from "@/app/(presentation-generator)/upload/type";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";

interface PresentationGenUploadState {
  config: PresentationConfig | null;

  files: any;

  /** Selected template id (built-in, custom-<uuid>, or encoded Template V2 identity). */
  selectedTemplate: string | null;

  /** Selected theme-preset id (a DEFAULT_THEMES id) applied to the deck after generation; null = none. */
  selectedTheme: string | null;

  /** Authored mode only: opt into the vision-QA self-correction pass (higher quality,
   * slower — it adds a second authoring round). Off by default for speed. */
  authoredVisionQa: boolean;

  /** Authored mode only: selected server-authored visual style id. */
  authoredStyle: string;
}

const initialState: PresentationGenUploadState = {
  config: null,
  files: [],
  selectedTemplate: "adaptive", // default = content-first adaptive composer; see presentation-templates/select.ts
  selectedTheme: null, // null = no preset; deck keeps the default (theme picked later in the editor)
  authoredVisionQa: false,
  authoredStyle: "default",
};

export const presentationGenUploadSlice = createSlice({
  name: "pptGenUpload",
  initialState,
  reducers: {
    setPptGenUploadState: (
      state,
      action: PayloadAction<Partial<PresentationGenUploadState>>
    ) => {
      // Partial merge: only assign keys present in the payload so callers can
      // update a single field (e.g. selectedTemplate) without wiping the rest.
      const {
        config,
        files,
        selectedTemplate,
        selectedTheme,
        authoredVisionQa,
        authoredStyle,
      } = action.payload;
      if (config !== undefined) state.config = config;
      if (files !== undefined) state.files = files;
      if (selectedTemplate !== undefined) state.selectedTemplate = selectedTemplate;
      if (selectedTheme !== undefined) state.selectedTheme = selectedTheme;
      if (authoredVisionQa !== undefined) state.authoredVisionQa = authoredVisionQa;
      if (authoredStyle !== undefined) state.authoredStyle = authoredStyle;
    },

  },
});

export const { setPptGenUploadState, } =
  presentationGenUploadSlice.actions;
export default presentationGenUploadSlice.reducer;
