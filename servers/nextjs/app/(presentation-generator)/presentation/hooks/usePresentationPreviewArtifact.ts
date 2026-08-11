"use client";

import { useEffect, useMemo, useState } from "react";

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
  const [state, setState] = useState<PresentationPreviewArtifactState>({
    artifact: null,
    error: null,
    pending: false,
  });

  useEffect(() => {
    let cancelled = false;
    if (!enabled) {
      setState({ artifact: null, error: null, pending: false });
      return () => {
        cancelled = true;
      };
    }
    setState({ artifact: null, error: null, pending: true });
    void actions.renderProduction("preview", presentation).then(
      (artifact) => {
        if (!cancelled) setState({ artifact, error: null, pending: false });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState({
            artifact: null,
            error: error instanceof Error ? error : new Error(String(error)),
            pending: false,
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [actions, enabled, presentation]);

  return state;
}
