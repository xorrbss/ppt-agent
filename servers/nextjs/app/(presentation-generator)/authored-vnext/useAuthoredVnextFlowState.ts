"use client";
import { useReducer } from "react";
import { authoredVnextFlowReducer, createAuthoredVnextFlowState, authoredVnextSlideCount, type FlowEvent } from "@/lib/authored-vnext/flow";

export function useAuthoredVnextFlowState(slideCount: ReturnType<typeof authoredVnextSlideCount>) {
  return useReducer(
    (state: ReturnType<typeof createAuthoredVnextFlowState>, event: FlowEvent) =>
      authoredVnextFlowReducer(state, event).state,
    undefined,
    () => createAuthoredVnextFlowState(slideCount),
  );
}
