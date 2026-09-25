import { useCallback } from "react";
import { useGenerationWorkspace } from "../../generation-workspace/GenerationWorkspaceProvider";

const ANCHOR_SELECTOR = "[data-workspace-anchor-id]";

function anchorFor(container: HTMLElement) {
  const containerTop = container.getBoundingClientRect().top;
  const anchor = Array.from(
    container.querySelectorAll<HTMLElement>(ANCHOR_SELECTOR),
  ).find((element) => element.getBoundingClientRect().bottom > containerTop);
  if (!anchor) return undefined;
  const id = anchor.dataset.workspaceAnchorId;
  if (!id) return undefined;
  return {
    id,
    offset: anchor.getBoundingClientRect().top - containerTop,
  };
}

export function useWorkspaceScrollContinuity(presentationId: string) {
  const workspace = useGenerationWorkspace();
  const views = workspace?.views;
  const updateView = workspace?.updateView;
  const restore = useCallback(
    (container?: HTMLElement | null) => {
      const view = views?.current[presentationId];
      const savedAnchor = view?.scrollAnchor;
      if (!container || !savedAnchor) return view?.scrollTop;
      const anchor = Array.from(
        container.querySelectorAll<HTMLElement>(ANCHOR_SELECTOR),
      ).find((element) => element.dataset.workspaceAnchorId === savedAnchor.id);
      if (!anchor) return view.scrollTop;
      const containerTop = container.getBoundingClientRect().top;
      const next =
        container.scrollTop +
        anchor.getBoundingClientRect().top -
        containerTop -
        savedAnchor.offset;
      return Math.min(
        Math.max(next, 0),
        Math.max(0, container.scrollHeight - container.clientHeight),
      );
    },
    [presentationId, views, updateView],
  );
  const remember = useCallback(
    (value: number | HTMLElement) => {
      const scrollTop = typeof value === "number" ? value : value.scrollTop;
      const patch = {
        scrollTop,
        ...(typeof value === "number" ? {} : { scrollAnchor: anchorFor(value) }),
      };
      if (updateView) updateView(presentationId, patch);
      else if (views) {
        views.current[presentationId] = { ...views.current[presentationId], ...patch };
      }
    },
    [presentationId, updateView, views],
  );
  return { restore, remember };
}
