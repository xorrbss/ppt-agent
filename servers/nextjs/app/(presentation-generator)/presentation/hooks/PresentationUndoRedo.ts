import { useCallback } from "react";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { redo, undo } from "@/store/slices/undoRedoSlice";
import { useKeyboardShortcut } from "../../hooks/use-keyboard-shortcut";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import type { WorkspaceCommandDispatcher } from "../workspace";

export const usePresentationUndoRedo = (
  enabled = true,
  workspaceCommand?: WorkspaceCommandDispatcher
) => {
  const dispatch = useDispatch();
  const undoRedoState = useSelector((state: RootState) => state.undoRedo);
  const { presentationData } = useSelector(
    (state: RootState) => state.presentationGeneration
  );

  const workspaceHistoryEnabled = Boolean(workspaceCommand);
  const historyEnabled = enabled || workspaceHistoryEnabled;
  // The durable history is loaded by the server-side restore operation. Keep
  // these controls actionable after reconnect even though Redux has no local
  // snapshots; a missing server restore point becomes a durable failed card.
  const canUndo = workspaceHistoryEnabled || undoRedoState.past.length > 0;
  const canRedo = workspaceHistoryEnabled || undoRedoState.future.length > 0;

  const applySlidesSnapshot = useCallback(
    (slidesSnapshot: unknown) => {
      if (!presentationData || !Array.isArray(slidesSnapshot)) {
        return;
      }

      const clonedSlides = JSON.parse(JSON.stringify(slidesSnapshot));
      dispatch(
        setPresentationData({
          ...presentationData,
          slides: clonedSlides,
        })
      );
    },
    [dispatch, presentationData]
  );

  const onUndo = useCallback(() => {
    if (workspaceCommand) {
      void workspaceCommand("이전 서버 버전으로 복원해 줘", {
        commandType: "version.restore",
        context: { strategy: "previous", source: "undo" },
      }).catch(() => undefined);
      return;
    }
    if (!enabled || !canUndo) {
      return;
    }

    const previousState = undoRedoState.past[undoRedoState.past.length - 1];
    if (!previousState) {
      return;
    }

    dispatch(undo());
    applySlidesSnapshot(previousState.slides);
  }, [applySlidesSnapshot, canUndo, dispatch, enabled, undoRedoState.past, workspaceCommand]);

  const onRedo = useCallback(() => {
    if (workspaceCommand) {
      // A restore checkpoints the pre-restore deck as the newest server
      // version, so restoring "previous" again is the durable redo path.
      void workspaceCommand("복원 직전 서버 버전으로 다시 실행해 줘", {
        commandType: "version.restore",
        context: { strategy: "previous", source: "redo" },
      }).catch(() => undefined);
      return;
    }
    if (!enabled || !canRedo) {
      return;
    }

    const nextState = undoRedoState.future[0];
    if (!nextState) {
      return;
    }

    dispatch(redo());
    applySlidesSnapshot(nextState.slides);
  }, [applySlidesSnapshot, canRedo, dispatch, enabled, undoRedoState.future, workspaceCommand]);

  // Handle undo (Ctrl + Z)
  useKeyboardShortcut(
    ["z"],
    (e) => {
      if (historyEnabled && e.ctrlKey && !e.shiftKey && canUndo) {
        e.preventDefault();
        onUndo();
      }
    }
  );

  // Handle redo (Ctrl + Shift + Z)
  useKeyboardShortcut(
    ["z"],
    (e) => {
      if (historyEnabled && e.ctrlKey && e.shiftKey && canRedo) {
        e.preventDefault();
        onRedo();
      }
    }
  );

  // Handle redo (Ctrl + Y)
  useKeyboardShortcut(
    ["y"],
    (e) => {
      if (historyEnabled && e.ctrlKey && canRedo) {
        e.preventDefault();
        onRedo();
      }
    }
  );

  return { onUndo, onRedo, canUndo, canRedo };
};
