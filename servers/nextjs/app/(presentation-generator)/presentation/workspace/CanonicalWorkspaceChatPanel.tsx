"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { canonicalConversationTransport } from "../../adapters/upstream_chat/canonicalConversationTransport";
import { ConversationController } from "../conversation/ConversationController";
import { buildConversationContext } from "../conversation/context";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "./types";
import { declaredTargetSlides, type WorkspaceAgentSlideFocus } from "./useWorkspaceAgentFocus";

/** Workspace composition only; HTTP and legacy/upstream details stay outside the domain UI. */
export function CanonicalWorkspaceChatPanel(props: {
  presentationId: string;
  currentSlide?: number;
  currentSlideId?: string;
  visibleSlideIds?: string[];
  onPresentationPublished?: (revision?: number) => void;
  onCommandDispatcherReady?: (dispatcher: WorkspaceCommandDispatcher | null) => void;
  onQualityReviewRequesterReady?: (requester: WorkspaceQualityReviewRequester | null) => void;
  onPageRemakeRequesterReady?: (requester: WorkspacePageRemakeRequester | null) => void;
  onAgentActivityChange?: (working: boolean) => void;
  onAgentSlideFocus?: (focus: WorkspaceAgentSlideFocus) => void;
}) {
  const t = useTranslations("conversation");
  const {
    presentationId,
    currentSlide,
    currentSlideId,
    visibleSlideIds,
    onPresentationPublished,
    onCommandDispatcherReady,
    onQualityReviewRequesterReady,
    onPageRemakeRequesterReady,
    onAgentActivityChange,
    onAgentSlideFocus,
  } = props;
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const focusSequenceRef = useRef(0);
  useEffect(() => {
    const update = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const context = useMemo(() => buildConversationContext({
    viewportWidth: viewport.width,
    viewportHeight: viewport.height,
    currentSlideId,
    currentSlideIndex: currentSlide,
    visibleSlideIds,
  }), [
    currentSlide,
    currentSlideId,
    visibleSlideIds,
    viewport.height,
    viewport.width,
  ]);

  const handleAgentActivityChange = useCallback((working: boolean) => {
    onAgentActivityChange?.(working);
    if (!working || !Number.isInteger(currentSlide) || (currentSlide ?? -1) < 0) return;
    focusSequenceRef.current += 1;
    onAgentSlideFocus?.({
      slideIndex: currentSlide!,
      eventId: `canonical-conversation-focus-${focusSequenceRef.current}`,
    });
  }, [currentSlide, onAgentActivityChange, onAgentSlideFocus]);

  return <ConversationController
    presentationId={presentationId}
    transport={canonicalConversationTransport}
    context={context}
    onPresentationPublished={onPresentationPublished}
    onAgentActivityChange={handleAgentActivityChange}
    onSendReady={(send) => {
      const dispatch: WorkspaceCommandDispatcher | null = send
        ? async (command, options) => {
            const targets = declaredTargetSlides(options);
            const slideIndex = targets[0] ?? currentSlide;
            if (Number.isInteger(slideIndex) && (slideIndex ?? -1) >= 0) {
              focusSequenceRef.current += 1;
              onAgentSlideFocus?.({
                slideIndex: slideIndex!,
                eventId: `canonical-conversation-focus-${focusSequenceRef.current}`,
              });
            }
            await send(command, {
              commandType: options?.commandType,
              context: options?.context,
              assetIds: options?.assetIds,
            });
          }
        : null;
      onCommandDispatcherReady?.(dispatch);
      onQualityReviewRequesterReady?.(
        send
          ? (slideIndex) => void dispatch?.(
              slideIndex === undefined
                ? t("reviewAll")
                : t("reviewSlide", { slide: slideIndex + 1 }),
              {
                commandType: "quality.review",
                context: slideIndex === undefined
                  ? { scope: "all" }
                  : { scope: "current", slide_indices: [slideIndex] },
              },
            )
          : null,
      );
      onPageRemakeRequesterReady?.(
        send
          ? (requested) => {
              // This panel hosts no preview dialog, so a batch selection has no
              // safe equivalent here: publishing one page of it would apply
              // work the user never saw.
              if (Array.isArray(requested)) return;
              void dispatch?.(t("remakeSlide", { slide: requested + 1 }), {
                commandType: "slide.regenerate",
                context: { slide_index: requested },
              });
            }
          : null,
      );
    }}
  />;
}
