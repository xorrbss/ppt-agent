import { Theme } from "@/app/(presentation-generator)/services/api/types";
import { Slide } from "@/app/(presentation-generator)/types/slide";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import {
  setAdaptiveBlockText,
  setAdaptiveChartBlock,
  deleteAdaptiveUnit as crudDeleteUnit,
  moveAdaptiveUnit as crudMoveUnit,
  addAdaptiveUnit as crudAddUnit,
  type AdaptiveChartPatch,
} from "@/lib/adaptiveBlockEdit";
import type { ImageEditorProperties } from "@/components/slide-editor/images/imageCrop";
import type {
  PresentationCapabilities,
  PresentationProfile,
} from "@/lib/presentation-profile";

/** Object/array container addressed by a dotted+indexed content data path. */
type PathContainer = Record<string | number, unknown>;

/** Chart object stored at `slide.content.chart`. Structural mirror of the
 *  in-tree editor's ChartData (kept local so the store does not depend on a
 *  component module). */
interface SlideChart {
  type?: "bar" | "line" | "area";
  data: Array<{ name?: string; value?: number }>;
}

/** A stored slide as the editor keeps it. The concrete shape depends on the
 *  template family (classic `content.*`, adaptive `content.blocks[]`,
 *  template-v2), so re-indexing only relies on it being an object. */
type SlideRecord = Record<string, unknown>;

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
  // Deliberately untyped: every consumer reads template-family-specific paths
  // off `slides[i].content` (classic fields, adaptive `blocks[]`, template-v2
  // elements). Narrowing this needs a discriminated slide model first.
  slides: any;
  theme: Theme | null;
  generation_mode?: string;
  profile?: PresentationProfile;
  capabilities?: PresentationCapabilities;
  revision?: number;
  lifecycle_status?: string;
  fonts?: Record<string, string> | null;
}

export interface ChatHtmlSelection {
  slideId?: string | null;
  slideIndex: number;
  slideNumber: number;
  html: string;
  elementTag?: string | null;
  selectedText?: string;
  selectedAt: number;
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
  enableHtmlSelector: boolean;
  chatHtmlSelection: ChatHtmlSelection | null;
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
  enableHtmlSelector: false,
  chatHtmlSelection: null,
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
      if (state.presentation_id !== action.payload) {
        state.chatHtmlSelection = null;
      }
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
      state.chatHtmlSelection = null;
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
      state.chatHtmlSelection = null;
    },
    setEnableHtmlSelector: (state, action: PayloadAction<boolean>) => {
      state.enableHtmlSelector = action.payload;
      if (!action.payload) state.chatHtmlSelection = null;
    },
    setChatHtmlSelection: (
      state,
      action: PayloadAction<ChatHtmlSelection>
    ) => {
      state.chatHtmlSelection = action.payload;
    },
    clearChatHtmlSelection: (state) => {
      state.chatHtmlSelection = null;
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

    // Deterministic text edit for adaptive slides, keyed by data-block-id (no
    // fragile string matching). blockId is one of: a top-level block id (e.g.
    // "title"), a nested item id (bullets/column items, e.g. "b1"/"col1.1"), or
    // a dotted block field (e.g. "s1.value", "card1.title", "step1.label").
    updateAdaptiveBlock: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        blockId: string;
        content: string;
      }>
    ) => {
      const { slideIndex, blockId, content } = action.payload;
      const slide = state.presentationData?.slides?.[slideIndex];
      setAdaptiveBlockText(slide?.content?.blocks, blockId, content);
    },

    // Update an adaptive chart block's type / data / series by block id. Chart
    // shape changes (add/remove rows or series, type switch) can't go through the
    // text-only updateAdaptiveBlock.
    updateAdaptiveBlockChart: (
      state,
      action: PayloadAction<{
        slideIndex: number;
        blockId: string;
        patch: AdaptiveChartPatch;
      }>
    ) => {
      const { slideIndex, blockId, patch } = action.payload;
      const slide = state.presentationData?.slides?.[slideIndex];
      setAdaptiveChartBlock(slide?.content?.blocks, blockId, patch);
    },

    // Adaptive block/item CRUD (P4b) — unitId is a top-level block id or a
    // nested item id; helpers locate the owning array (blocks[] or items[]).
    deleteAdaptiveUnit: (
      state,
      action: PayloadAction<{ slideIndex: number; unitId: string }>
    ) => {
      const slide = state.presentationData?.slides?.[action.payload.slideIndex];
      crudDeleteUnit(slide?.content?.blocks, action.payload.unitId);
    },
    moveAdaptiveUnit: (
      state,
      action: PayloadAction<{ slideIndex: number; unitId: string; delta: number }>
    ) => {
      const slide = state.presentationData?.slides?.[action.payload.slideIndex];
      crudMoveUnit(slide?.content?.blocks, action.payload.unitId, action.payload.delta);
    },
    addAdaptiveUnit: (
      state,
      action: PayloadAction<{ slideIndex: number; afterUnitId: string }>
    ) => {
      const slide = state.presentationData?.slides?.[action.payload.slideIndex];
      crudAddUnit(slide?.content?.blocks, action.payload.afterUnitId);
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

        // Helper function to set nested property value for images
        const setNestedImageValue = (
          obj: PathContainer,
          path: string,
          url: string,
          promptText?: string
        ) => {
          const keys = path.split(/[.\[\]]+/).filter(Boolean);
          let current = obj;

          // Navigate to the parent object
          for (let i = 0; i < keys.length - 1; i++) {
            const key = keys[i];
            if (isNaN(Number(key))) {
              if (!current[key]) {
                current[key] = {};
              }
              current = current[key] as PathContainer;
            } else {
              const index = Number(key);
              if (!current[index]) {
                current[index] = {};
              }
              current = current[index] as PathContainer;
            }
          }

          // Set the image properties
          const finalKey = keys[keys.length - 1];
          const target = isNaN(Number(finalKey)) ? current[finalKey] : current[Number(finalKey)];
          const targetRecord =
            target && typeof target === 'object'
              ? (target as PathContainer)
              : undefined;

          // Preserve existing properties if the target already exists
          const updatedValue = {
            ...(targetRecord ?? {}),
            __image_url__: url,
            __image_prompt__: promptText || (targetRecord?.__image_prompt__) || ''
          };

          if (isNaN(Number(finalKey))) {
            current[finalKey] = updatedValue;
          } else {
            current[Number(finalKey)] = updatedValue;
          }

        };

        // Update the slide image
        if (dataPath && slide.content) {
          setNestedImageValue(slide.content, dataPath, imageUrl, prompt);
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

        // Helper function to set nested property value for icons
        const setNestedIconValue = (
          obj: PathContainer,
          path: string,
          url: string,
          queryText?: string
        ) => {
          const keys = path.split(/[.\[\]]+/).filter(Boolean);
          let current = obj;

          // Navigate to the parent object
          for (let i = 0; i < keys.length - 1; i++) {
            const key = keys[i];
            if (isNaN(Number(key))) {
              if (!current[key]) {
                current[key] = {};
              }
              current = current[key] as PathContainer;
            } else {
              const index = Number(key);
              if (!current[index]) {
                current[index] = {};
              }
              current = current[index] as PathContainer;
            }
          }

          // Set the icon properties
          const finalKey = keys[keys.length - 1];
          const target = isNaN(Number(finalKey)) ? current[finalKey] : current[Number(finalKey)];
          const targetRecord =
            target && typeof target === 'object'
              ? (target as PathContainer)
              : undefined;

          // Preserve existing properties if the target already exists
          const updatedValue = {
            ...(targetRecord ?? {}),
            __icon_url__: url,
            __icon_query__: queryText || (targetRecord?.__icon_query__) || ''
          };

          if (isNaN(Number(finalKey))) {
            current[finalKey] = updatedValue;
          } else {
            current[Number(finalKey)] = updatedValue;
          }
        };

        // Update the slide icon
        if (dataPath && slide.content) {
          setNestedIconValue(slide.content, dataPath, iconUrl, query);
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
  setEnableHtmlSelector,
  setChatHtmlSelection,
  clearChatHtmlSelection,
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
  updateAdaptiveBlock,
  updateAdaptiveBlockChart,
  deleteAdaptiveUnit,
  moveAdaptiveUnit,
  addAdaptiveUnit,
  updateSlideImage,
  updateImageProperties,
  updateSlideIcon,
  addNewSlide,
  updateTheme,
} = presentationGenerationSlice.actions;

export default presentationGenerationSlice.reducer;
