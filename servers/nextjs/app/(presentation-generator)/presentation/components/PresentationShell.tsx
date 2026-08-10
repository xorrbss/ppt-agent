"use client";

import React, { createContext, useContext } from "react";
import { RENDER_ARTIFACT_CONTRACT } from "../../../../lib/render-service/contracts";

import type {
  PresentationFeatureSlotName,
  PresentationFeatureViewModel,
} from "./presentationFeatureSlots";

const FeatureViewContext = createContext<PresentationFeatureViewModel | null>(null);

export function PresentationFeatureSlot({
  name,
  children,
}: {
  readonly name: PresentationFeatureSlotName;
  readonly children: React.ReactNode;
}) {
  const viewModel = useContext(FeatureViewContext);
  if (!viewModel || !viewModel.slots[name]) return null;
  return <>{children}</>;
}

/**
 * A deliberately thin shell: it owns page chrome and slot policy while the
 * existing editor and canonical conversation panel retain their own behavior.
 */
export default function PresentationShell({
  title,
  background,
  viewModel,
  children,
}: {
  readonly title: string;
  readonly background: string;
  readonly viewModel: PresentationFeatureViewModel;
  readonly children: React.ReactNode;
}) {
  return (
    <FeatureViewContext.Provider value={viewModel}>
      <main
        className="h-screen h-dvh w-full max-w-full overflow-clip font-syne"
        aria-labelledby="presentation-editor-title"
        data-presentation-driver={viewModel.driverId ?? "unresolved"}
        data-review-required={viewModel.reviewRequired || undefined}
      >
        <h1 id="presentation-editor-title" className="sr-only">
          {title}
        </h1>
        <div
          id="presentation-slides-wrapper"
          style={{ background }}
          className="relative flex h-full w-full min-w-0 max-w-full flex-col overflow-clip"
        >
          {children}
        </div>
      </main>
    </FeatureViewContext.Provider>
  );
}

/** The adapters are observable without exposing browser/runtime internals. */
export function PresentationRuntimeDependencies({
  dependencies,
}: {
  readonly dependencies: readonly string[];
}) {
  return (
    <div
      aria-hidden="true"
      className="hidden"
      data-testid="presentation-runtime-dependencies"
      data-runtime-dependencies={dependencies.join(",")}
      data-render-artifact-contract={RENDER_ARTIFACT_CONTRACT}
    />
  );
}
