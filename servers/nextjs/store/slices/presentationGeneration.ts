import { Theme } from "@/app/(presentation-generator)/services/api/types";
import { Slide } from "@/app/(presentation-generator)/types/slide";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import type { ImageEditorProperties } from "@/components/slide-editor/images/imageCrop";
import type {
  PresentationCapabilities,
  PresentationProfile,
} from "@/lib/presentation-profile";
import { setNestedMediaValue } from "./presentationGenerationMedia";
import type { PathContainer, SlideChart, SlideRecord } from "./presentationGenerationTypes";

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
  isSlidesRendered: boolean;
  isLayoutLoading: boolean;
}

const initialState: PresentationGenerationState = {
  presentation_id: null,
  outlines: [],
  isSlidesRendered: false,
  isLayoutLoading: false,
  isLoading: false,
  isStreaming: null,
  error: null,
  presentationData: null,
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
    setLayoutLoading: (state, action: PayloadAction<boolean>) => {
      state.isLayoutLoading = action.payload;
    },
    // Presentation ID
    setPresentationId: (state, action: PayloadAction<string>) => {
      state.presentation_id = action.payload;
      state.error = null;
    },
    // Slides rendered
    setSlidesRendered: (state, action: PayloadAction<boolean>) => {
      state.isSlidesRendered = action.payload;
    },
    // Error
    setError: (state, action: PayloadAction<string>) => {
      state.error = action.payload;
      state.isLoading = false;
    },
    // Clear presentation data
    clearPresentationData: (state) => {
      state.presentationData = null;
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
    updateSlideHtmlContent: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        html: string;
        slideId?: string | null;
      }>
    ) => {
      const slides = state.presentationData?.slides;
      if (!Array.isArray(slides)) return;

      const slide =
        (action.payload.slideId
          ? slides.find((item: Slide) => item.id === action.payload.slideId)
          : undefined) ??
        slides.find((item: Slide) => item.index === action.payload.slideIndex) ??
        slides[action.payload.slideIndex];
      if (slide) slide.html_content = action.payload.html;
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

    // Replace a slide's chart object (type + data points) wholesale. Used by the
    // in-tree chart editor: type switches and adding/removing points change the
    // array shape, which the path-based updateSlideContent can't express, and the
    // values must stay numbers (not the strings updateSlideContent stores).
    updateSlideChart: (
      state,
      action: PayloadAction<{ slideIndex: number; chart: SlideChart }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides &&
        state.presentationData.slides[action.payload.slideIndex]
      ) {
        const slide = state.presentationData.slides[action.payload.slideIndex];
        slide.content = { ...slide.content, chart: action.payload.chart };
      }
    },

    // Update slide content at specific data path (for Tiptap text editing)
    updateSlideContent: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        dataPath: string;
        content: string;
      }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides &&
        state.presentationData.slides[action.payload.slideIndex]
      ) {
        const slide = state.presentationData.slides[action.payload.slideIndex];
        const { dataPath, content } = action.payload;

        // Helper function to set nested property value
        const setNestedValue = (
          obj: PathContainer,
          path: string,
          value: string
        ) => {
          const keys = path.split(/[.\[\]]+/).filter(Boolean);
          let current = obj;

          // Navigate to the parent object
          for (let i = 0; i < keys.length - 1; i++) {
            const key = keys[i];
            if (isNaN(Number(key))) {
              // String key
              if (!current[key]) {
                current[key] = {};
              }
              current = current[key] as PathContainer;
            } else {
              // Array index
              const index = Number(key);
              if (!current[index]) {
                current[index] = {};
              }
              current = current[index] as PathContainer;
            }
          }

          // Set the final value
          const finalKey = keys[keys.length - 1];
          if (isNaN(Number(finalKey))) {
            current[finalKey] = value;
          } else {
            current[Number(finalKey)] = value;
          }
        };

        // Update the slide content
        if (dataPath && slide.content) {
          setNestedValue(slide.content, dataPath, content);
        }
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

    // Update slide image at specific data path
    updateSlideImage: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        dataPath: string;
        imageUrl: string;
        prompt?: string;
      }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides &&
        state.presentationData.slides[action.payload.slideIndex]
      ) {
        const slide = state.presentationData.slides[action.payload.slideIndex];
        const { dataPath, imageUrl, prompt } = action.payload;

        if (dataPath && slide.content) {
          setNestedMediaValue(slide.content, dataPath, imageUrl, prompt, "__image_url__", "__image_prompt__");
        }

        // Also update the images array if it exists
        if (slide.images && Array.isArray(slide.images)) {
          const imageIndex = parseInt(dataPath.split('[')[1]?.split(']')[0]) || 0;
          if (slide.images[imageIndex] !== undefined) {
            slide.images[imageIndex] = imageUrl;
          }
        }
      }
    },

    updateImageProperties: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        itemIndex: number;
        properties: ImageEditorProperties;
      }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides &&
        state.presentationData.slides[action.payload.slideIndex]
      ) {
        const slide = state.presentationData.slides[action.payload.slideIndex];
        const { itemIndex, properties } = action.payload;
        slide['properties'] = {
          ...slide.properties,
          [itemIndex]: properties
        };

      }
    },

    // Update slide icon at specific data path
    updateSlideIcon: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        dataPath: string;
        iconUrl: string;
        query?: string;
      }>
    ) => {
      if (
        state.presentationData &&
        state.presentationData.slides &&
        state.presentationData.slides[action.payload.slideIndex]
      ) {
        const slide = state.presentationData.slides[action.payload.slideIndex];
        const { dataPath, iconUrl, query } = action.payload;

        if (dataPath && slide.content) {
          setNestedMediaValue(slide.content, dataPath, iconUrl, query, "__icon_url__", "__icon_query__");
        }

        // Also update the icons array if it exists
        if (slide.icons && Array.isArray(slide.icons)) {
          const iconIndex = parseInt(dataPath.split('[')[1]?.split(']')[0]) || 0;
          if (slide.icons[iconIndex] !== undefined) {
            slide.icons[iconIndex] = iconUrl;
          }
        }
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
  setLayoutLoading,
  setPresentationId,
  setSlidesRendered,
  setError,
  clearPresentationData,
  clearOutlines,
  deleteSlideOutline,
  setPresentationData,
  updateTitle,
  setOutlines,
  // slides operations
  addSlide,
  updateSlide,
  updateSlideHtmlContent,
  deletePresentationSlide,
  updateSlideContent,
  updateSlideSpeakerNote,
  updateSlideChart,
  updateSlideImage,
  updateImageProperties,
  updateSlideIcon,
  addNewSlide,
  updateTheme,
} = presentationGenerationSlice.actions;

export default presentationGenerationSlice.reducer;
