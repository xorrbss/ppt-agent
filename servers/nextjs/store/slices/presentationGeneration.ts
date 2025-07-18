import { Slide } from "@/app/(presentation-generator)/types/slide";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";



export interface SlideOutline {
  title?: string;
  body?: string;
}


export interface PresentationData {
  id: string;
  language: string;
  layout: {
    name: string;
    ordered: boolean;
    slides: any[];
  };
  n_slides: number;
  title: string;
  slides: any;
}

interface PresentationGenerationState {
  presentation_id: string | null;
  isLoading: boolean;
  isStreaming: boolean | null;
  outlines: SlideOutline[];
  error: string | null;
  presentationData: PresentationData | null;
  isSlidesRendered: boolean;
}

const initialState: PresentationGenerationState = {
  presentation_id: null,
  outlines: [],
  isSlidesRendered: false,
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
      state.presentation_id = null;
      state.error = null;
      state.isLoading = false;
    },
    // Set outlines
    setOutlines: (state, action: PayloadAction<SlideOutline[]>) => {
      state.outlines = action.payload;
    },
    // Set presentation data
    setPresentationData: (state, action: PayloadAction<PresentationData>) => {
      state.presentationData = action.payload;
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
          (slide: any, idx: number) => ({
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
          (slide: any, idx: number) => ({
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
        const setNestedValue = (obj: any, path: string, value: string) => {
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
              current = current[key];
            } else {
              // Array index
              const index = Number(key);
              if (!current[index]) {
                current[index] = {};
              }
              current = current[index];
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
  },
});

export const {
  setStreaming,
  setLoading,
  setPresentationId,
  setSlidesRendered,
  setError,
  clearPresentationData,
  deleteSlideOutline,
  setPresentationData,
  setOutlines,
  // slides operations
  addSlide,
  updateSlide,
  deletePresentationSlide,
  updateSlideContent,
} = presentationGenerationSlice.actions;

export default presentationGenerationSlice.reducer;
