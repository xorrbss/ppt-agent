import { Theme } from "@/app/(presentation-generator)/services/api/types";
import { Slide } from "@/app/(presentation-generator)/types/slide";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import type {
  PresentationCapabilities,
  PresentationProfile,
} from "@/lib/presentation-profile";
import type { SlideRecord } from "./presentationGenerationTypes";
import type { FlaggedSlideMap } from "@/app/(presentation-generator)/presentation/utils/flaggedSlides";

export interface PresentationData {
  id: string;
  language: string;
  layout: {
    name: string;
    ordered: boolean;
    slides: unknown[];
  };
  n_slides: number;
  title: string;
  // Deliberately untyped while Authored and Template V2 consumers read their
  // own server-issued slide contracts. Narrowing this needs a discriminated
  // slide model first.
  slides: any;
  theme: Theme | null;
  profile?: PresentationProfile;
  capabilities?: PresentationCapabilities;
  version?: string;
  revision?: number;
  lifecycle_status?: string;
  source_sha256?: string;
  fonts?: Record<string, string> | null;
}

interface PresentationGenerationState {
  presentation_id: string | null;
  isLoading: boolean;
  isStreaming: boolean | null;
  outlines: { content: string }[];
  error: string | null;
  presentationData: PresentationData | null;
  isLayoutLoading: boolean;
  /**
   * Slides the authored pipeline published flagged (D7 terminal fallback /
   * author-stage rescue), keyed by slide id. Built once per load from the
   * persisted quality-gate receipt so an in-session reorder keeps the badge.
   */
  flaggedSlides: FlaggedSlideMap;
}

const initialState: PresentationGenerationState = {
  presentation_id: null,
  outlines: [],
  isLayoutLoading: false,
  isLoading: false,
  isStreaming: null,
  error: null,
  presentationData: null,
  flaggedSlides: {},
};

const presentationGenerationSlice = createSlice({
  name: "presentationGeneration",
  initialState,
  reducers: {
    setStreaming: (state, action: PayloadAction<boolean>) => {
      state.isStreaming = action.payload;
    },
    // Loading
    setLoading: (state, action: PayloadAction<boolean>) => {
      state.isLoading = action.payload;
    },
    // Presentation ID
    setPresentationId: (state, action: PayloadAction<string>) => {
      state.presentation_id = action.payload;
      state.error = null;
    },
    // Error
    setError: (state, action: PayloadAction<string>) => {
      state.error = action.payload;
      state.isLoading = false;
    },
    // Clear presentation data
    clearPresentationData: (state) => {
      state.presentationData = null;
      state.flaggedSlides = {};
    },
    setFlaggedSlides: (state, action: PayloadAction<FlaggedSlideMap>) => {
      state.flaggedSlides = action.payload;
    },
    clearOutlines: (state) => {
      state.outlines = [];
    },
    // Set outlines
    setOutlines: (state, action: PayloadAction<{ content: string }[]>) => {
      state.outlines = action.payload;
    },
    // Set presentation data
    setPresentationData: (state, action: PayloadAction<PresentationData>) => {
      state.presentationData = action.payload;
    },
    updateTitle: (state, action: PayloadAction<string>) => {
      if (state.presentationData) {
        state.presentationData.title = action.payload;
      }
    },
    deleteSlideOutline: (state, action: PayloadAction<{ index: number }>) => {
      if (state.outlines) {
        // Remove the slide at the given index
        state.outlines = state.outlines.filter(
          (_, idx) => idx !== action.payload.index
        );
      }
    },
    // SLIDE OPERATIONS
    addSlide: (
      state,
      action: PayloadAction<{ slide: Slide; index: number }>
    ) => {
      if (state.presentationData?.slides) {
        // Insert the new slide at the specified index
        state.presentationData.slides.splice(
          action.payload.index,
          0,
          action.payload.slide
        );

        // Update indices for all slides to ensure they remain sequential
        state.presentationData.slides = state.presentationData.slides.map(
          (slide: SlideRecord, idx: number) => ({
            ...slide,
            index: idx,
          })
        );
      }
    },
    deletePresentationSlide: (state, action: PayloadAction<number>) => {
      if (state.presentationData) {
        state.presentationData.slides.splice(action.payload, 1);
        state.presentationData.slides = state.presentationData.slides.map(
          (slide: SlideRecord, idx: number) => ({
            ...slide,
            index: idx,
          })
        );
      }
    },
    updateSlide: (
      state,
      action: PayloadAction<{ index: number; slide: Slide }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides[action.payload.index]
      ) {
        state.presentationData.slides[action.payload.index] =
          action.payload.slide;
      }
    },
    // Update a slide's speaker note (presenter-only text; persisted by autosave).
    updateSlideSpeakerNote: (
      state,
      action: PayloadAction<{ slideIndex: number; speakerNote: string }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides &&
        state.presentationData.slides[action.payload.slideIndex]
      ) {
        state.presentationData.slides[action.payload.slideIndex].speaker_note =
          action.payload.speakerNote;
      }
    },

    addNewSlide: (state, action: PayloadAction<{ slideData: SlideRecord; index: number }>) => {
      if (state.presentationData?.slides) {
        // Insert the new slide at the specified index + 1 (after current slide)
        state.presentationData.slides.splice(action.payload.index + 1, 0, action.payload.slideData);

        // Update indices for all slides to ensure they remain sequential
        state.presentationData.slides = state.presentationData.slides.map(
          (slide: SlideRecord, idx: number) => ({
            ...slide,
            index: idx,
          })
        );
      }
    },

    updateTheme: (state, action: PayloadAction<Theme | null>) => {
      if (state.presentationData) {
        state.presentationData['theme'] = action.payload;
      }
    },
  },

});

export const {
  setStreaming,
  setLoading,
  setPresentationId,
  setError,
  clearPresentationData,
  clearOutlines,
  deleteSlideOutline,
  setPresentationData,
  setFlaggedSlides,
  updateTitle,
  setOutlines,
  // slides operations
  addSlide,
  updateSlide,
  deletePresentationSlide,
  updateSlideSpeakerNote,
  addNewSlide,
  updateTheme,
} = presentationGenerationSlice.actions;

export default presentationGenerationSlice.reducer;
