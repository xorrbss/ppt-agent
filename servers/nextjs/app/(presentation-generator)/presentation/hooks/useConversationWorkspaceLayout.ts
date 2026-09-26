"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEventHandler,
  type PointerEvent,
  type PointerEventHandler,
  type RefCallback,
} from "react";
import {
  readConversationWorkspacePreferences,
  writeConversationWorkspacePreferences,
  type ConversationWorkspacePane,
} from "./conversationWorkspaceState";

const SPLIT_MIN_WIDTH = 968;
const THUMBNAIL_AUTO_COLLAPSE_WIDTH = 1080;
const PRESENTATION_MIN_WIDTH = 600;
const SEPARATOR_WIDTH = 8;
const CHAT_WIDTH_PREFERRED_RATIO = 0.35;
const CHAT_WIDTH_MIN = 360;
const CHAT_WIDTH_MAX = 600;
const KEYBOARD_RESIZE_STEP = 16;

export interface UseConversationWorkspaceLayoutOptions {
  presentationId: string;
  /** A completed, renderable result has been loaded into the presentation page. */
  resultReady: boolean;
  /** Changes only for a newly completed result, never for a timeline replay. */
  completionKey?: string | null;
  /** Allows a host to keep separate session-only layout namespaces. */
  storageKeyPrefix?: string;
}

export interface ConversationWorkspaceSeparatorProps {
  "aria-controls": string;
  "aria-label": string;
  "aria-orientation": "vertical";
  "aria-valuemax": number;
  "aria-valuemin": number;
  "aria-valuenow": number;
  "aria-valuetext": string;
  ref: RefCallback<HTMLDivElement>;
  role: "separator";
  tabIndex: number;
  onKeyDown: KeyboardEventHandler<HTMLDivElement>;
  onPointerCancel: PointerEventHandler<HTMLDivElement>;
  onPointerDown: PointerEventHandler<HTMLDivElement>;
  onPointerMove: PointerEventHandler<HTMLDivElement>;
  onPointerUp: PointerEventHandler<HTMLDivElement>;
}

export interface ConversationWorkspaceLayout {
  containerRef: RefCallback<HTMLDivElement>;
  containerWidth: number;
  isSplit: boolean;
  showThumbnails: boolean;
  thumbnailPanelOpen: boolean;
  setThumbnailPanelOpen: (open: boolean) => void;
  chatWidth: number;
  activePane: ConversationWorkspacePane;
  setActivePane: (pane: ConversationWorkspacePane) => void;
  chatCollapsed: boolean;
  toggleChatCollapsed: () => void;
  separatorProps: ConversationWorkspaceSeparatorProps;
}

export function getConversationWorkspaceChatWidthBounds(containerWidth: number) {
  const availableMaximum = Math.min(
    CHAT_WIDTH_MAX,
    containerWidth - PRESENTATION_MIN_WIDTH - SEPARATOR_WIDTH,
  );
  const maximum = Math.max(CHAT_WIDTH_MIN, availableMaximum);
  return { minimum: CHAT_WIDTH_MIN, maximum };
}

export function resolveConversationWorkspaceChatWidth(
  preferredWidth: number | undefined,
  containerWidth: number,
): number {
  const { minimum, maximum } =
    getConversationWorkspaceChatWidthBounds(containerWidth);
  const width = preferredWidth ?? containerWidth * CHAT_WIDTH_PREFERRED_RATIO;
  return Math.round(Math.min(Math.max(width, minimum), maximum));
}

export function useConversationWorkspaceLayout({
  presentationId,
  resultReady,
  completionKey,
  storageKeyPrefix,
}: UseConversationWorkspaceLayoutOptions): ConversationWorkspaceLayout {
  const [containerElement, setContainerElement] =
    useState<HTMLDivElement | null>(null);
  const containerRef = useCallback<RefCallback<HTMLDivElement>>(
    (element) => setContainerElement(element),
    [],
  );
  const dragStartRef = useRef<{
    pointerId: number;
    x: number;
    width: number;
  } | null>(null);
  const separatorElementRef = useRef<HTMLDivElement | null>(null);
  const restoreTabFocusRef = useRef(false);
  const measuredWidthRef = useRef(0);
  const handledCompletionKeyRef = useRef<string | null>(null);
  const currentPresentationIdRef = useRef(presentationId);
  const [containerWidth, setContainerWidth] = useState(0);
  const [preferredChatWidth, setPreferredChatWidth] = useState<
    number | undefined
  >(undefined);
  const [activePane, setActivePaneState] =
    useState<ConversationWorkspacePane>("chat");
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const [thumbnailPanelOpen, setThumbnailPanelOpen] = useState(false);
  const [lastHandledCompletionKey, setLastHandledCompletionKey] = useState<
    string | undefined
  >(undefined);
  const [loadedPresentationId, setLoadedPresentationId] = useState<string | null>(
    null,
  );

  useLayoutEffect(() => {
    if (currentPresentationIdRef.current !== presentationId) {
      currentPresentationIdRef.current = presentationId;
      handledCompletionKeyRef.current = null;
    }
  }, [presentationId]);

  const setActivePane = useCallback((pane: ConversationWorkspacePane) => {
    setActivePaneState(pane);
  }, []);

  const setSeparatorElement = useCallback<RefCallback<HTMLDivElement>>(
    (element) => {
      separatorElementRef.current = element;
    },
    [],
  );

  useEffect(() => {
    if (!containerElement) return;

    const updateWidth = () => {
      const nextWidth = containerElement.clientWidth;
      const wasSplit = measuredWidthRef.current >= SPLIT_MIN_WIDTH;
      const willSplit = nextWidth >= SPLIT_MIN_WIDTH;

      if (wasSplit && !willSplit) {
        const activeElement = document.activeElement;
        const chatPane = document.getElementById("workspace-chat-pane");
        const slidesPane = document.getElementById("presentation-slides-pane");
        if (activeElement === separatorElementRef.current) {
          restoreTabFocusRef.current = true;
        } else if (
          activeElement instanceof HTMLElement &&
          chatPane?.contains(activeElement)
        ) {
          setActivePane("chat");
        } else if (
          activeElement instanceof HTMLElement &&
          slidesPane?.contains(activeElement)
        ) {
          setActivePane("slides");
        }
      }

      measuredWidthRef.current = nextWidth;
      setContainerWidth(nextWidth);
    };
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(containerElement);
    return () => observer.disconnect();
  }, [containerElement, setActivePane]);

  useEffect(() => {
    const preferences = readConversationWorkspacePreferences(
      presentationId,
      storageKeyPrefix,
    );
    setPreferredChatWidth(preferences?.chatWidth);
    setActivePane(preferences?.activePane ?? "chat");
    setChatCollapsed(preferences?.chatCollapsed ?? false);
    setLastHandledCompletionKey(preferences?.lastHandledCompletionKey);
    handledCompletionKeyRef.current =
      preferences?.lastHandledCompletionKey ?? null;
    setThumbnailPanelOpen(false);
    setLoadedPresentationId(presentationId);
  }, [presentationId, storageKeyPrefix]);

  useEffect(() => {
    if (loadedPresentationId !== presentationId) return;
    writeConversationWorkspacePreferences(
      presentationId,
      {
        chatWidth: preferredChatWidth,
        activePane,
        chatCollapsed,
        lastHandledCompletionKey,
      },
      storageKeyPrefix,
    );
  }, [
    activePane,
    chatCollapsed,
    loadedPresentationId,
    lastHandledCompletionKey,
    preferredChatWidth,
    presentationId,
    storageKeyPrefix,
  ]);

  useEffect(() => {
    if (
      loadedPresentationId !== presentationId ||
      !resultReady ||
      !completionKey ||
      lastHandledCompletionKey === completionKey ||
      handledCompletionKeyRef.current === completionKey
    ) {
      return;
    }
    handledCompletionKeyRef.current = completionKey;
    setLastHandledCompletionKey(completionKey);
    const chatPane = document.getElementById("workspace-chat-pane");
    const activeElement = document.activeElement;
    const inputFocused =
      activeElement instanceof HTMLElement &&
      chatPane?.contains(activeElement) &&
      activeElement.matches("input, textarea, [contenteditable='true']");
    const readingHistory = Boolean(
      chatPane?.querySelector("[data-follow-latest='false']"),
    );
    if (inputFocused || readingHistory) return;
    setActivePane("slides");
    setChatCollapsed(false);
  }, [
    completionKey,
    lastHandledCompletionKey,
    loadedPresentationId,
    presentationId,
    resultReady,
  ]);

  const isSplit = containerWidth >= SPLIT_MIN_WIDTH;
  const chatWidth = resolveConversationWorkspaceChatWidth(
    preferredChatWidth,
    containerWidth,
  );
  const showThumbnails =
    containerWidth >= THUMBNAIL_AUTO_COLLAPSE_WIDTH || thumbnailPanelOpen;

  useEffect(() => {
    if (containerWidth >= THUMBNAIL_AUTO_COLLAPSE_WIDTH) {
      setThumbnailPanelOpen(false);
    }
  }, [containerWidth]);

  useLayoutEffect(() => {
    if (isSplit || !restoreTabFocusRef.current || !resultReady) return;
    restoreTabFocusRef.current = false;
    document
      .getElementById(`compact-${activePane}-tab`)
      ?.focus({ preventScroll: true });
  }, [activePane, isSplit, resultReady]);

  const setBoundedChatWidth = useCallback(
    (nextWidth: number) => {
      setPreferredChatWidth(
        resolveConversationWorkspaceChatWidth(nextWidth, containerWidth),
      );
    },
    [containerWidth],
  );

  const finishDrag = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (dragStartRef.current?.pointerId !== event.pointerId) return;
    dragStartRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const onPointerDown = useCallback<PointerEventHandler<HTMLDivElement>>(
    (event) => {
      if (!isSplit || event.button !== 0) return;
      event.preventDefault();
      dragStartRef.current = {
        pointerId: event.pointerId,
        x: event.clientX,
        width: chatWidth,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [chatWidth, isSplit],
  );

  const onPointerMove = useCallback<PointerEventHandler<HTMLDivElement>>(
    (event) => {
      const drag = dragStartRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      setBoundedChatWidth(drag.width - (event.clientX - drag.x));
    },
    [setBoundedChatWidth],
  );

  const onKeyDown = useCallback<KeyboardEventHandler<HTMLDivElement>>(
    (event) => {
      if (!isSplit) return;
      const step = event.shiftKey
        ? KEYBOARD_RESIZE_STEP * 3
        : KEYBOARD_RESIZE_STEP;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setBoundedChatWidth(chatWidth + step);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        setBoundedChatWidth(chatWidth - step);
      } else if (event.key === "Home") {
        event.preventDefault();
        setBoundedChatWidth(CHAT_WIDTH_MIN);
      } else if (event.key === "End") {
        event.preventDefault();
        setBoundedChatWidth(CHAT_WIDTH_MAX);
      }
    },
    [chatWidth, isSplit, setBoundedChatWidth],
  );

  const toggleChatCollapsed = useCallback(() => {
    setChatCollapsed((collapsed) => !collapsed);
  }, []);

  const separatorProps = useMemo<ConversationWorkspaceSeparatorProps>(
    () => ({
      ref: setSeparatorElement,
      role: "separator",
      tabIndex: isSplit ? 0 : -1,
      "aria-label": "대화 영역 너비 조절",
      "aria-controls": "workspace-chat-pane",
      "aria-orientation": "vertical",
      "aria-valuemin": CHAT_WIDTH_MIN,
      "aria-valuemax":
        getConversationWorkspaceChatWidthBounds(containerWidth).maximum,
      "aria-valuenow": chatWidth,
      "aria-valuetext": `${Math.round(chatWidth)}px`,
      onPointerDown,
      onPointerMove,
      onPointerUp: finishDrag,
      onPointerCancel: finishDrag,
      onKeyDown,
    }),
    [
      chatWidth,
      containerWidth,
      finishDrag,
      isSplit,
      onKeyDown,
      onPointerDown,
      onPointerMove,
      setSeparatorElement,
    ],
  );

  return {
    containerRef,
    containerWidth,
    isSplit,
    showThumbnails,
    thumbnailPanelOpen,
    setThumbnailPanelOpen,
    chatWidth,
    activePane,
    setActivePane,
    chatCollapsed,
    toggleChatCollapsed,
    separatorProps,
  };
}
