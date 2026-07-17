"use client";
import { createContext, useContext } from "react";

export interface EditableTextContextValue {
  slideIndex: number;
  isEditMode: boolean;
  // Persist an edited field. `path` is the dot/bracket path into the slide content
  // (e.g. "title" or "items[2].heading") — the same shape updateSlideContent takes.
  onEdit: (path: string, content: string) => void;
}

// Default = read-only, no-op. So a layout using <EditableText> still renders
// correctly when mounted without a provider (e.g. dashboard thumbnails).
const EditableTextContext = createContext<EditableTextContextValue>({
  slideIndex: -1,
  isEditMode: false,
  onEdit: () => {},
});

export const EditableTextProvider = EditableTextContext.Provider;

export const useEditableText = (): EditableTextContextValue =>
  useContext(EditableTextContext);
