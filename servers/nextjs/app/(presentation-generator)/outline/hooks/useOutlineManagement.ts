import { useCallback } from "react";
import { useDispatch } from "react-redux";
import { arrayMove } from "@dnd-kit/sortable";
import { setOutlines } from "@/store/slices/presentationGeneration";

export const useOutlineManagement = (outlines: string[] | null) => {
  const dispatch = useDispatch();

  const handleDragEnd = useCallback((event: any) => {
    const { active, over } = event;

    if (!active || !over || !outlines) return;

    if (active.id !== over.id) {
      const oldIndex = outlines.findIndex((item) => item === active.id);
      const newIndex = outlines.findIndex((item) => item === over.id);
      const reorderedArray = arrayMove(outlines, oldIndex, newIndex);
      dispatch(setOutlines(reorderedArray));
    }
  }, [outlines, dispatch]);

  const handleAddSlide = useCallback(() => {
    if (!outlines) return;

    const updatedOutlines = [...outlines, "Outline title"];
    dispatch(setOutlines(updatedOutlines));
  }, [outlines, dispatch]);

  return { handleDragEnd, handleAddSlide };
}; 