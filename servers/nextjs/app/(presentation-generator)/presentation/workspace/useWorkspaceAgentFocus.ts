"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  WorkspaceCommandOptions,
  WorkspacePresentationChange,
  WorkspaceTimelineAnchor,
} from "./types";

/** Operation statuses that mean the runner still owns the deck. */
const IN_FLIGHT_STATUSES = new Set([
  "queued",
  "waiting_for_assets",
  "blocked",
  "running",
  "cancelling",
]);

/** Command kinds that name their pages when the command is dispatched. */
const SLIDE_SCOPED_COMMAND_TYPES = new Set([
  "slide.edit",
  "slide.regenerate",
  "slide.delete",
]);

function slideIndexList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is number => Number.isInteger(entry) && entry >= 0,
  );
}

function singleSlideIndex(...values: unknown[]): number | null {
  const found = values.find(
    (value): value is number => Number.isInteger(value) && (value as number) >= 0,
  );
  return found ?? null;
}

/**
 * Slides a command declares up front.
 *
 * The workspace event stream reports stage and percent only, so a page the
 * runner happens to touch mid-flight is not observable. Deriving the target
 * from the dispatched command keeps the canvas honest: a request that names no
 * page — a whole-deck review, a free-form message — reports no target rather
 * than guessing the selected slide.
 */
export function declaredTargetSlides(
  options?: WorkspaceCommandOptions,
): number[] {
  const context = options?.context ?? {};
  const commandType = options?.commandType;
  if (commandType === "quality.review") {
    if (context.scope === "all") return [];
    return slideIndexList(context.slide_indices);
  }
  if (!commandType || !SLIDE_SCOPED_COMMAND_TYPES.has(commandType)) return [];
  const explicit = slideIndexList(context.slide_indices);
  if (explicit.length > 0) return explicit;
  const single = singleSlideIndex(context.slide_index, context.current_slide);
  return single === null ? [] : [single];
}

export interface WorkspaceAgentSlideFocus {
  slideIndex: number;
  eventId: string;
}

interface UseWorkspaceAgentFocusOptions {
  presentationId: string;
  submitting: boolean;
  items: WorkspaceTimelineAnchor[];
  onActivityChange?: (active: boolean) => void;
  onSlideFocus?: (focus: WorkspaceAgentSlideFocus) => void;
}

export interface WorkspaceAgentFocusController {
  followEnabled: boolean;
  toggleFollow: () => void;
  /** Record the pages a command claimed, before its operation exists. */
  noteDispatch: (options?: WorkspaceCommandOptions) => void;
  /** Follow the pages a committed revision actually changed. */
  notePresentationChange: (change: WorkspacePresentationChange) => void;
}

export function useWorkspaceAgentFocus({
  presentationId,
  submitting,
  items,
  onActivityChange,
  onSlideFocus,
}: UseWorkspaceAgentFocusOptions): WorkspaceAgentFocusController {
  const [followEnabled, setFollowEnabled] = useState(true);
  const followEnabledRef = useRef(followEnabled);
  const focusSequenceRef = useRef(0);
  const onSlideFocusRef = useRef(onSlideFocus);

  useEffect(() => {
    followEnabledRef.current = followEnabled;
  }, [followEnabled]);

  useEffect(() => {
    onSlideFocusRef.current = onSlideFocus;
  }, [onSlideFocus]);

  const operationInFlight = useMemo(
    () =>
      items.some(
        (item) =>
          item.anchorType === "operation" &&
          IN_FLIGHT_STATUSES.has(item.status),
      ),
    [items],
  );
  const active = submitting || operationInFlight;

  useEffect(() => {
    onActivityChange?.(active);
  }, [active, onActivityChange]);

  useEffect(() => {
    setFollowEnabled(true);
  }, [presentationId]);

  const emitFocus = useCallback((slideIndex: number) => {
    if (!followEnabledRef.current || slideIndex < 0) return;
    focusSequenceRef.current += 1;
    onSlideFocusRef.current?.({
      slideIndex,
      eventId: `workspace-focus-${focusSequenceRef.current}`,
    });
  }, []);

  const noteDispatch = useCallback(
    (options?: WorkspaceCommandOptions) => {
      const targets = declaredTargetSlides(options);
      if (targets.length === 0) return;
      emitFocus(targets[0]);
    },
    [emitFocus],
  );

  const notePresentationChange = useCallback(
    (change: WorkspacePresentationChange) => {
      const fixed = slideIndexList(change.fixedSlideIndexes);
      if (fixed.length === 0) return;
      emitFocus(fixed[0]);
    },
    [emitFocus],
  );

  const toggleFollow = useCallback(
    () => setFollowEnabled((previous) => !previous),
    [],
  );

  return { followEnabled, toggleFollow, noteDispatch, notePresentationChange };
}
