import { configureStore } from "@reduxjs/toolkit";

import presentationGenerationReducer from "./slices/presentationGeneration";
import pptGenUploadReducer from "./slices/presentationGenUpload";
import userConfigReducer from "./slices/userConfig";
import undoRedoReducer from "./slices/undoRedoSlice";
// Factory so tests can model a page reload (a browser reload rebuilds the
// store from scratch — nothing here is persisted).
export const makeStore = () =>
  configureStore({
    reducer: {
      presentationGeneration: presentationGenerationReducer,
      pptGenUpload: pptGenUploadReducer,
      userConfig: userConfigReducer,
      undoRedo: undoRedoReducer,
    },
  });

export const store = makeStore();

// Infer the `RootState` and `AppDispatch` types from the store itself
export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
