"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { RenderArtifact } from "@/lib/render-service";
import { createPresentationRenderActionFactory } from "../components/presentationRenderGateway";

export interface PresentationPreviewArtifactState {
  readonly artifact: RenderArtifact | null;
  readonly error: Error | null;
  readonly pending: boolean;
}

export function usePresentationPreviewArtifact(
  presentation: unknown,
  enabled: boolean,
): PresentationPreviewArtifactState {
  const actions = useMemo(() => createPresentationRenderActionFactory(), []);
  const previousPresentationId = useRef<unknown>(null);
  const [state, setState] = useState<PresentationPreviewArtifactState>({
    artifact: null,
    error: null,
    pending: false,
  });

  useEffect(() => {
    let cancelled = false;
    const id = presentation && typeof presentation === "object" && "id" in presentation
      ? presentation.id : null;
    const samePresentation = id !== null && previousPresentationId.current === id;
    previousPresentationId.current = id;
    if (!enabled) {
      setState({ artifact: null, error: null, pending: false });
      return () => {
        cancelled = true;
      };
    }
    setState((previous) => ({ artifact: samePresentation ? previous.artifact : null, error: null, pending: true }));
    void actions.renderProduction("preview", presentation).then(
      (artifact) => {
        if (!cancelled) setState({ artifact, error: null, pending: false });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState((previous) => ({
            artifact: samePresentation ? previous.artifact : null,
            error: error instanceof Error ? error : new Error(String(error)),
            pending: false,
          }));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [actions, enabled, presentation]);

  return state;
}
