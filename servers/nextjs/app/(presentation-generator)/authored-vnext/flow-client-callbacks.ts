import type { Dispatch } from "react";

import type {
  AuthoredWorkbenchCallbacks,
  WorkbenchTypedCommand,
} from "@/components/authored-vnext";
import { generationClient, type GenerationDeckSnapshot } from "@/lib/authored-vnext/flow";
import type { DeckMasterContractV1 } from "@/lib/authored-vnext/master/contracts";
import {
  adaptDeckSnapshotToWorkbenchDocument,
  type ElementId,
  type OperationId,
  type WorkbenchState,
} from "@/lib/authored-vnext/workbench";
import type { WorkbenchAction } from "@/lib/authored-vnext/workbench/reducer";

interface CallbackArgs {
  accept: () => void;
  canonicalDeck: GenerationDeckSnapshot | null;
  contract: DeckMasterContractV1 | undefined;
  createOperationId: () => string;
  dispatch: Dispatch<WorkbenchAction>;
  enqueue: (command: WorkbenchTypedCommand) => void;
  exportDeck: () => void;
  onDiscardError: (code: string) => void;
  reject: () => void;
  reorder: (slideId: string, target: number) => void;
  rollback: () => void;
  setCanonicalDeck: (deck: GenerationDeckSnapshot) => void;
  setSelectedElementId: (elementId: ElementId) => void;
  workbench: WorkbenchState;
}

export function flowClientCallbacks({ accept, canonicalDeck, contract, createOperationId, dispatch, enqueue, exportDeck, onDiscardError, reject, reorder, rollback, setCanonicalDeck, setSelectedElementId, workbench }: CallbackArgs): AuthoredWorkbenchCallbacks {
  const refresh = (type: "conflict/rebaseSucceeded" | "conflict/discarded") => {
    if (!canonicalDeck || !contract) return;
    void generationClient.getDeck(canonicalDeck.deckId).then(({ deck }) => {
      setCanonicalDeck(deck);
      dispatch({ type, serverDeck: adaptDeckSnapshotToWorkbenchDocument(deck, contract) });
    }).catch((error) => {
      const code = error instanceof Error ? error.message : "authored_vnext_rebase_failed";
      if (type === "conflict/rebaseSucceeded") dispatch({ type: "conflict/rebaseFailed", message: code });
      else onDiscardError(code);
    });
  };
  return {
    onSelectSlide: (slideId) => dispatch({ type: "slide/activate", slideId: slideId as `slide:${string}` }),
    onReorderSlide: reorder,
    onSelectElement: (elementId) => setSelectedElementId(elementId as ElementId),
    onCommand: enqueue,
    onUndo: () => workbench.undoStack.length > 0 ? dispatch({ type: "history/undo", operationId: createOperationId() as OperationId, createdAt: new Date().toISOString() }) : rollback(),
    onRedo: () => dispatch({ type: "history/redo", operationId: createOperationId() as OperationId, createdAt: new Date().toISOString() }),
    onAcceptRegeneration: accept,
    onRejectRegeneration: reject,
    onRebaseConflict: () => { dispatch({ type: "conflict/rebaseStarted" }); refresh("conflict/rebaseSucceeded"); },
    onDismissConflict: () => refresh("conflict/discarded"),
    onRetry: () => {
      if (!canonicalDeck || !contract) return;
      void generationClient.getDeck(canonicalDeck.deckId).then(({ deck }) => {
        setCanonicalDeck(deck);
        dispatch({ type: "deck/loadSucceeded", deck: adaptDeckSnapshotToWorkbenchDocument(deck, contract) });
      }).catch((error) => onDiscardError(error instanceof Error ? error.message : "authored_vnext_reload_failed"));
    },
    onRequestExport: exportDeck,
  };
}
