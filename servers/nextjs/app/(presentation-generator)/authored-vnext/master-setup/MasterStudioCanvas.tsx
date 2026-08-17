"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import type {
  BrandAsset,
  DeckMasterContractV1,
  LayoutKind,
} from "@/lib/authored-vnext/master/contracts";

import MasterKonvaCanvas from "./MasterKonvaCanvas";
import {
  applyMasterCanvasTransaction,
  masterCanvasClipboardItems,
  masterContractToCanvasScene,
  type MasterCanvasClipboardItem,
  type MasterCanvasTransaction,
} from "./master-canvas-adapter";
import MasterWysiwygWorkspace from "./MasterWysiwygWorkspace";
import type { PptAnalysisReviewProps } from "./PptAnalysisReviewPanel";

export interface MasterStudioCanvasProps {
  contract: DeckMasterContractV1;
  layoutKind: LayoutKind;
  logoUrl?: string | null;
  /** Asset upload stays an element-level action; the legacy logo form is optional. */
  onUploadAsset?: (file: File) => Promise<BrandAsset>;
  resolveAssetUrl?: (asset: BrandAsset) => string | null | undefined;
  /** Evidence/review controls stay outside the semantic master contract. */
  analysisReview?: PptAnalysisReviewProps;
  onContractChange: (next: DeckMasterContractV1) => void;
}

const HISTORY_LIMIT = 30;
const CLIPBOARD_STORAGE_PREFIX = "presenton:master-canvas-clipboard:v1:";

interface StoredMasterCanvasClipboard {
  version: 1;
  masterId: string;
  items: MasterCanvasClipboardItem[];
}

function readStoredClipboard(masterId: string): MasterCanvasClipboardItem[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(`${CLIPBOARD_STORAGE_PREFIX}${masterId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredMasterCanvasClipboard>;
    if (parsed.version !== 1 || parsed.masterId !== masterId || !Array.isArray(parsed.items)) {
      return null;
    }
    const items = parsed.items.filter((item): item is MasterCanvasClipboardItem =>
      Boolean(item) && typeof item === "object" &&
      ((item.kind === "semantic" && Boolean(item.element)) ||
        (item.kind === "slot" && Boolean(item.slot))),
    );
    return items.length ? items : null;
  } catch {
    return null;
  }
}

function storeClipboard(masterId: string, items: MasterCanvasClipboardItem[]) {
  const payload: StoredMasterCanvasClipboard = { version: 1, masterId, items };
  window.localStorage.setItem(
    `${CLIPBOARD_STORAGE_PREFIX}${masterId}`,
    JSON.stringify(payload),
  );
}

function contractsMatch(
  left: DeckMasterContractV1,
  right: DeckMasterContractV1,
) {
  return left === right || JSON.stringify(left) === JSON.stringify(right);
}

/**
 * Connects the shared Konva canvas to the semantic master contract. Konva node
 * state remains transient; only completed semantic transactions reach the
 * parent and therefore the draft/publish/native-PPTX pipeline.
 */
export default function MasterStudioCanvas({
  contract,
  layoutKind,
  logoUrl,
  onUploadAsset,
  resolveAssetUrl,
  analysisReview,
  onContractChange,
}: MasterStudioCanvasProps) {
  const t = useTranslations("authoredVnext.master.studio");
  const [history, setHistory] = useState<DeckMasterContractV1[]>([]);
  const [future, setFuture] = useState<DeckMasterContractV1[]>([]);
  const contractRef = useRef(contract);
  const historyRef = useRef<DeckMasterContractV1[]>([]);
  const futureRef = useRef<DeckMasterContractV1[]>([]);
  const [interactionError, setInteractionError] = useState(false);
  const [editRequest, setEditRequest] = useState<{ elementId: string; nonce: number } | null>(null);
  const editRequestNonceRef = useRef(0);
  const [interactionResetNonce, setInteractionResetNonce] = useState(0);
  const [preview, setPreview] = useState(false);
  const [hasClipboard, setHasClipboard] = useState(false);
  const [activeLayoutId, setActiveLayoutId] = useState<string>(layoutKind);
  const [selected, setSelected] = useState<import("./master-canvas-adapter").MasterCanvasElement[]>([]);
  useEffect(() => {
    contractRef.current = contract;
  }, [contract]);
  useEffect(() => {
    const storageKey = `${CLIPBOARD_STORAGE_PREFIX}${contract.masterId}`;
    const refreshClipboard = () => {
      setHasClipboard(Boolean(readStoredClipboard(contract.masterId)));
    };
    const handleStorage = (event: StorageEvent) => {
      if (event.key === storageKey) refreshClipboard();
    };
    refreshClipboard();
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [contract.masterId]);
  useEffect(() => {
    setActiveLayoutId(layoutKind);
    setSelected([]);
    setEditRequest(null);
    setInteractionResetNonce((current) => current + 1);
  }, [layoutKind]);
  useEffect(() => {
    const valid = new Set([
      ...Object.keys(contract.layouts),
      ...(contract.customLayouts || []).map((layout) => layout.id),
    ]);
    if (!valid.has(activeLayoutId)) setActiveLayoutId(layoutKind);
  }, [activeLayoutId, contract.customLayouts, contract.layouts, layoutKind]);
  const scene = useMemo(
    () => masterContractToCanvasScene(contract, activeLayoutId),
    [contract, activeLayoutId],
  );
  const sharedLogoTargetCount = useMemo(() => {
    const groupId = selected.length === 1 && selected[0].kind === "image"
      ? selected[0].content?.sharedGroupId
      : undefined;
    return groupId
      ? (contract.semanticElements ?? []).filter((element) => element.content.sharedGroupId === groupId).length
      : undefined;
  }, [contract.semanticElements, selected]);

  const commit = useCallback(
    (transaction: MasterCanvasTransaction | { type: string; [key: string]: unknown }) => {
      try {
        const currentContract = contractRef.current;
        const canvasTransaction = transaction as MasterCanvasTransaction;
        const next = applyMasterCanvasTransaction(currentContract, activeLayoutId, canvasTransaction);
        if (contractsMatch(next, currentContract)) return;
        const nextHistory = [
          ...historyRef.current.slice(-(HISTORY_LIMIT - 1)),
          currentContract,
        ];
        historyRef.current = nextHistory;
        futureRef.current = [];
        contractRef.current = next;
        setHistory(nextHistory);
        setFuture([]);
        setInteractionError(false);
        // Keep inspector metadata (notably shared-logo override state) aligned
        // with the semantic snapshot that just became the undo history head.
        setSelected((current) => {
          const nextScene = masterContractToCanvasScene(next, activeLayoutId);
          return current.flatMap((item) => {
            const refreshed = nextScene.elements.find((candidate) => candidate.id === item.id);
            return refreshed ? [refreshed] : [];
          });
        });
        if (canvasTransaction.type === "insertElement") {
          setEditRequest({
            elementId: canvasTransaction.element.id,
            nonce: ++editRequestNonceRef.current,
          });
        }
        onContractChange(next);
      } catch {
        // A gesture must never make the editor crash. Strict contract
        // validation still runs when the draft is validated or published.
        setInteractionError(true);
      }
    },
    [activeLayoutId, onContractChange],
  );

  const undo = useCallback(() => {
    const previous = historyRef.current.at(-1);
    if (!previous) return;
    const currentContract = contractRef.current;
    const nextHistory = historyRef.current.slice(0, -1);
    const nextFuture = [currentContract, ...futureRef.current].slice(0, HISTORY_LIMIT);
    historyRef.current = nextHistory;
    futureRef.current = nextFuture;
    contractRef.current = previous;
    setHistory(nextHistory);
    setFuture(nextFuture);
    setEditRequest(null);
    setInteractionResetNonce((current) => current + 1);
    setInteractionError(false);
    onContractChange(previous);
  }, [onContractChange]);

  const redo = useCallback(() => {
    const next = futureRef.current[0];
    if (!next) return;
    const currentContract = contractRef.current;
    const nextFuture = futureRef.current.slice(1);
    const nextHistory = [
      ...historyRef.current.slice(-(HISTORY_LIMIT - 1)),
      currentContract,
    ];
    historyRef.current = nextHistory;
    futureRef.current = nextFuture;
    contractRef.current = next;
    setFuture(nextFuture);
    setHistory(nextHistory);
    setEditRequest(null);
    setInteractionResetNonce((current) => current + 1);
    setInteractionError(false);
    onContractChange(next);
  }, [onContractChange]);

  const consumeEditRequest = useCallback(
    (request: { elementId: string; nonce: number }) => {
      setEditRequest((current) =>
        current?.nonce === request.nonce ? null : current,
      );
    },
    [],
  );

  const changeLayout = useCallback((layoutId: string) => {
    setActiveLayoutId(layoutId);
    setSelected([]);
    setEditRequest(null);
    setInteractionResetNonce((current) => current + 1);
    setInteractionError(false);
  }, []);

  const copySelection = useCallback((elementIds: string[]) => {
    try {
      const currentContract = contractRef.current;
      const items = masterCanvasClipboardItems(
        currentContract,
        activeLayoutId,
        elementIds,
      );
      if (!items.length) return;
      storeClipboard(currentContract.masterId, items);
      setHasClipboard(true);
      setInteractionError(false);
    } catch {
      setInteractionError(true);
    }
  }, [activeLayoutId]);

  const pasteClipboard = useCallback(() => {
    const items = readStoredClipboard(contractRef.current.masterId);
    if (!items) {
      setHasClipboard(false);
      return;
    }
    commit({ type: "paste", items });
  }, [commit]);

  return (
    <div className="space-y-2">
      <MasterWysiwygWorkspace
        activeLayout={activeLayoutId}
        analysisReview={analysisReview}
        canRedo={future.length > 0}
        canUndo={history.length > 0}
        layouts={[...Object.entries(contract.layouts).map(([kind, layout]) => ({ selector: kind, id: layout.id, name: layout.name, required: true })), ...((contract.customLayouts || []).map((layout) => ({ selector: layout.id, id: layout.id, name: layout.name, required: false })))]}
        onLayoutChange={changeLayout}
        onPreviewChange={setPreview}
        onRedo={redo}
        onTransaction={commit}
        onUndo={undo}
        onUploadAsset={onUploadAsset}
        preview={preview}
        scene={scene}
        selected={selected}
        sharedLogoTargetCount={sharedLogoTargetCount}
        hasBoundCanonicalLogo={Boolean(contract.furniture.logo.assetId)}
      >
      <MasterKonvaCanvas
        editRequest={editRequest}
        interactionResetNonce={interactionResetNonce}
        scene={scene}
        titleLines={[]}
        body=""
        logoUrl={logoUrl}
        resolveAssetUrl={resolveAssetUrl}
        onTransaction={commit}
        onCopy={copySelection}
        onPaste={pasteClipboard}
        canPaste={hasClipboard}
        onSelectionChange={setSelected}
        onEditRequestConsumed={consumeEditRequest}
        onRedo={redo}
        onUndo={undo}
        preview={preview}
      />
      </MasterWysiwygWorkspace>
      {interactionError ? (
        <p className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="alert">
          {t("overlapWarning")}
        </p>
      ) : null}
    </div>
  );
}
