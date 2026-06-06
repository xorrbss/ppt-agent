import { PresentationConfig } from "@/app/(presentation-generator)/upload/type";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";

interface PresentationGenUploadState {
  config: PresentationConfig | null;

  files: any;

  /** Selected template id (built-in id or custom-<uuid>); rehydrated to an object at call sites. */
  selectedTemplate: string | null;
}

const initialState: PresentationGenUploadState = {
  config: null,
  files: [],
  selectedTemplate: "adaptive", // default = content-first adaptive composer; see presentation-templates/select.ts
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
      const { config, files, selectedTemplate } = action.payload;
      if (config !== undefined) state.config = config;
      if (files !== undefined) state.files = files;
      if (selectedTemplate !== undefined) state.selectedTemplate = selectedTemplate;
    },

  },
});

export const { setPptGenUploadState, } =
  presentationGenUploadSlice.actions;
export default presentationGenUploadSlice.reducer;
