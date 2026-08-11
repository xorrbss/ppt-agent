"use client";

import { useMemo } from "react";

import type { ServerIssuedPresentationProfile } from "@/lib/presentation-profile";
import {
  buildPresentationFeatureViewModel,
  type PresentationFeatureViewModel,
} from "../components/presentationFeatureSlots";

export function usePresentationPageController({
  runtime,
  workspaceReady,
  loading,
}: {
  readonly runtime: ServerIssuedPresentationProfile | null;
  readonly workspaceReady: boolean;
  readonly loading: boolean;
}): PresentationFeatureViewModel {
  return useMemo(
    () =>
      buildPresentationFeatureViewModel({
        runtime,
        workspaceReady,
        loading,
      }),
    [loading, runtime, workspaceReady],
  );
}
