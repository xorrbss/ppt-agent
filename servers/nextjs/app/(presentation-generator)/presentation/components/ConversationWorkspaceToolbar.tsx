"use client";

import { PanelLeft } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import SidePanel from "./SidePanel";

export function ConversationWorkspaceToolbar({
  split, activePane, onPaneChange, chatCollapsed, onToggleChat,
  showThumbnails, thumbnailsOpen, onThumbnailsOpenChange,
  selectedSlide, onSlideClick, presentationId, loading,
}: {
  split: boolean;
  activePane: "slides" | "chat";
  onPaneChange: (pane: "slides" | "chat") => void;
  chatCollapsed: boolean;
  onToggleChat: () => void;
  showThumbnails: boolean;
  thumbnailsOpen: boolean;
  onThumbnailsOpenChange: (open: boolean) => void;
  selectedSlide: number;
  onSlideClick: (index: number) => void;
  presentationId: string;
  loading: boolean;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-neutral-200 bg-white px-3 py-2">
      {!split && (
        <div className="flex min-w-0 flex-1 gap-1" role="tablist" aria-label="작업 영역">
          {(["slides", "chat"] as const).map((pane) => (
            <button key={pane} type="button" id={`compact-${pane}-tab`} role="tab"
              aria-selected={activePane === pane}
              aria-controls={pane === "slides" ? "presentation-slides-pane" : "workspace-chat-pane"}
              data-testid={`responsive-${pane}-tab`}
              onClick={() => onPaneChange(pane)}
              onKeyDown={(event) => {
                if (
                  event.key !== "ArrowLeft" &&
                  event.key !== "ArrowRight" &&
                  event.key !== "Home" &&
                  event.key !== "End"
                ) {
                  return;
                }
                event.preventDefault();
                const next =
                  event.key === "Home"
                    ? "slides"
                    : event.key === "End"
                      ? "chat"
                      : pane === "slides"
                        ? "chat"
                        : "slides";
                onPaneChange(next);
                document.getElementById(`compact-${next}-tab`)?.focus();
              }}
              tabIndex={activePane === pane ? 0 : -1}
              className={`min-h-10 min-w-0 flex-1 rounded-lg px-4 text-sm font-medium ${activePane === pane ? "bg-neutral-100 text-neutral-900" : "text-neutral-500 hover:bg-neutral-50"}`}>
              {pane === "slides" ? "PPT" : "채팅"}
            </button>
          ))}
        </div>
      )}
      {!showThumbnails && (
        <Sheet open={thumbnailsOpen} onOpenChange={onThumbnailsOpenChange}>
          <SheetTrigger asChild>
            <button type="button" data-testid="mobile-thumbnail-drawer-trigger"
              aria-label={thumbnailsOpen ? "슬라이드 목록 닫기" : "슬라이드 목록 열기"}
              className="inline-flex h-11 items-center gap-2 rounded-lg px-3 text-sm text-neutral-600 hover:bg-neutral-100">
              <PanelLeft className="h-4 w-4" aria-hidden="true" />슬라이드 목록
            </button>
          </SheetTrigger>
          <SheetContent side="left" data-testid="mobile-thumbnail-drawer"
            className="flex h-dvh w-[min(320px,calc(100vw-16px))] max-w-none flex-col gap-3 p-4 [&>button]:h-11 [&>button]:w-11">
            <SheetHeader className="pr-12 text-left">
              <SheetTitle>슬라이드 목록</SheetTitle>
              <SheetDescription>이동할 슬라이드를 선택하세요.</SheetDescription>
            </SheetHeader>
            <div className="min-h-0 flex-1 overflow-hidden pt-2">
              <SidePanel selectedSlide={selectedSlide} onSlideClick={onSlideClick}
                presentationId={presentationId} loading={loading} mutationsEnabled={false} displayMode="drawer" />
            </div>
          </SheetContent>
        </Sheet>
      )}
      {split && (
        <button type="button" aria-expanded={!chatCollapsed} aria-controls="workspace-chat-pane"
          data-testid="workspace-drawer-toggle" onClick={onToggleChat}
          className="ml-auto min-h-10 rounded-lg px-3 text-sm text-neutral-600 hover:bg-neutral-100">
          {chatCollapsed ? "채팅 열기" : "채팅 접기"}
        </button>
      )}
    </div>
  );
}
