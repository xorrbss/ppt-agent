"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type Konva from "konva";
import { useTranslations } from "next-intl";
import { Ellipse, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text, Transformer } from "react-konva";

import { CanvasCoreGuides, type CanvasCoreGuide } from "@/components/canvas-core/CanvasCoreGuides";
import { CanvasCoreZoomControls, useCanvasCoreViewport } from "@/components/canvas-core/CanvasCoreViewport";
import { useTemplateImage } from "@/app/template-v2-studio/[templateId]/TemplateV2CanvasHelpers";

import type {
  MasterCanvasElement,
  MasterCanvasScene,
  MasterCanvasTransaction,
} from "./master-canvas-adapter";

const MIN_SIZE = 16;
const SNAP_DISTANCE = 7;

type InlineEditor = { elementId: string; value: string } | null;

function boundedBounds(bounds: MasterCanvasElement["bounds"], scene: MasterCanvasScene) {
  const width = Math.max(MIN_SIZE, Math.min(scene.width, bounds.width));
  const height = Math.max(MIN_SIZE, Math.min(scene.height, bounds.height));
  return {
    x: Math.max(0, Math.min(scene.width - width, bounds.x)),
    y: Math.max(0, Math.min(scene.height - height, bounds.y)),
    width,
    height,
  };
}

function placeholderText(
  element: MasterCanvasElement,
  titleLines: string[],
  body: string,
  labels: { title: string; body: string; content: string },
) {
  if (element.placeholder?.role === "title") return titleLines.join("\n") || labels.title;
  if (element.placeholder?.role === "body") return body || labels.body;
  return labels.content;
}

function imageSource(
  element: MasterCanvasElement,
  logoUrl: string | null | undefined,
  resolveAssetUrl?: (asset: NonNullable<MasterCanvasElement["asset"]>) => string | null | undefined,
) {
  if (element.asset && resolveAssetUrl) return resolveAssetUrl(element.asset) ?? null;
  return element.kind === "image" ? logoUrl ?? null : null;
}

function CanvasElement({
  element,
  titleLines,
  body,
  placeholderLabels,
  logoUrl,
  resolveAssetUrl,
  imageFit,
  selected,
  interactive,
  showPlaceholderChrome,
  setNode,
  onSelect,
  onDoubleClick,
  onDragStart,
  onDragMove,
  onDragEnd,
}: {
  element: MasterCanvasElement;
  titleLines: string[];
  body: string;
  placeholderLabels: { title: string; body: string; content: string };
  logoUrl?: string | null;
  resolveAssetUrl?: (asset: NonNullable<MasterCanvasElement["asset"]>) => string | null | undefined;
  imageFit: "contain" | "cover";
  selected: boolean;
  interactive: boolean;
  showPlaceholderChrome: boolean;
  setNode: (id: string, node: Konva.Node | null) => void;
  onSelect: (element: MasterCanvasElement, additive: boolean) => void;
  onDoubleClick: (element: MasterCanvasElement) => void;
  onDragStart: (element: MasterCanvasElement, node: Konva.Node) => void;
  onDragMove: (element: MasterCanvasElement, node: Konva.Node) => void;
  onDragEnd: (element: MasterCanvasElement, node: Konva.Node) => void;
}) {
  const semantic = element as MasterCanvasElement & {
    kind?: string;
    style?: { fill?: string; stroke?: string; strokeWidth?: number; opacity?: number; shape?: "rect" | "ellipse"; textAlign?: "left" | "center" | "right"; verticalAlign?: "top" | "middle" | "bottom" };
    content?: { fit?: "contain" | "cover"; crop?: { x: number; y: number; width: number; height: number } };
  };
  const kind = semantic.kind || element.kind;
  const source = imageSource(element, logoUrl, resolveAssetUrl);
  const image = useTemplateImage(source);
  const common = {
    id: element.id,
    name: "master-canvas-element",
    x: element.bounds.x,
    y: element.bounds.y,
    width: element.bounds.width,
    height: element.bounds.height,
    rotation: (semantic as { rotation?: number }).rotation ?? element.rotation,
    draggable: interactive && element.editable,
    listening: interactive,
    ref: (node: Konva.Node | null) => setNode(element.id, node),
    onMouseDown: (event: Konva.KonvaEventObject<MouseEvent>) => {
      const additive = event.evt.shiftKey || event.evt.metaKey || event.evt.ctrlKey;
      if (!selected && !additive) onSelect(element, false);
    },
    onClick: (event: Konva.KonvaEventObject<MouseEvent>) => {
      event.cancelBubble = true;
      onSelect(element, event.evt.shiftKey || event.evt.metaKey || event.evt.ctrlKey);
    },
    onTap: (event: Konva.KonvaEventObject<TouchEvent>) => {
      event.cancelBubble = true;
      onSelect(element, false);
    },
    onDblClick: () => onDoubleClick(element),
    onDblTap: () => onDoubleClick(element),
    onDragStart: (event: Konva.KonvaEventObject<DragEvent>) => onDragStart(element, event.target),
    onDragMove: (event: Konva.KonvaEventObject<DragEvent>) => onDragMove(element, event.target),
    onDragEnd: (event: Konva.KonvaEventObject<DragEvent>) => onDragEnd(element, event.target),
  };
  const text = kind === "placeholder"
    ? placeholderText(element, titleLines, body, placeholderLabels)
    : element.kind === "pageNumber" ? element.text || "1" : element.text || "";
  const outline = selected ? "#2563eb" : kind === "placeholder" && showPlaceholderChrome ? "#10b981" : undefined;
  const fill = semantic.style?.fill ?? element.fill;
  const stroke = semantic.style?.stroke ?? element.stroke;
  const opacity = semantic.style?.opacity ?? 1;
  const crop = semantic.content?.crop;
  const cropWidth = image ? image.width * (crop?.width ?? 1) : 1;
  const cropHeight = image ? image.height * (crop?.height ?? 1) : 1;
  const imageScale = image
    ? imageFit === "cover"
      ? Math.max(element.bounds.width / Math.max(1, cropWidth), element.bounds.height / Math.max(1, cropHeight))
      : Math.min(element.bounds.width / Math.max(1, cropWidth), element.bounds.height / Math.max(1, cropHeight))
    : 1;

  if (kind === "line") {
    return <Line {...common} opacity={opacity} points={[0, element.bounds.height / 2, element.bounds.width, element.bounds.height / 2]} stroke={stroke || element.color} strokeWidth={semantic.style?.strokeWidth ?? Math.max(2, element.bounds.height)} hitStrokeWidth={16} />;
  }
  if (kind === "shape" && semantic.style?.shape === "ellipse") {
    return <Group {...common}><Ellipse opacity={opacity} x={element.bounds.width / 2} y={element.bounds.height / 2} radiusX={element.bounds.width / 2} radiusY={element.bounds.height / 2} fill={fill || "transparent"} stroke={outline || stroke} strokeWidth={selected ? 2 : semantic.style?.strokeWidth || 1} /></Group>;
  }
  if (kind === "rect" || kind === "shape") {
    return <Rect {...common} opacity={opacity} fill={fill || "transparent"} stroke={outline || stroke} strokeWidth={selected ? 2 : semantic.style?.strokeWidth || 0} />;
  }
  if (kind === "image") {
    return <Group {...common} opacity={opacity} clipX={0} clipY={0} clipWidth={element.bounds.width} clipHeight={element.bounds.height}>
      {image ? <KonvaImage image={image} cropX={image.width * (crop?.x ?? 0)} cropY={image.height * (crop?.y ?? 0)} cropWidth={cropWidth} cropHeight={cropHeight} width={cropWidth} height={cropHeight} scaleX={imageScale} scaleY={imageScale} x={(element.bounds.width - cropWidth * imageScale) / 2} y={(element.bounds.height - cropHeight * imageScale) / 2} /> : <Rect width={element.bounds.width} height={element.bounds.height} fill="#e2e8f0" />}
      {selected && <Rect width={element.bounds.width} height={element.bounds.height} stroke="#2563eb" strokeWidth={2} listening={false} />}
    </Group>;
  }
  if (kind === "aiEditZone") {
    const zone = element.aiEditZone;
    const instruction = zone?.instruction?.trim();
    const pattern = zone?.pattern && zone.pattern !== "auto" ? zone.pattern : zone?.contentType;
    const patternLabels: Record<string, string> = {
      numberedCards: "번호 카드",
      bulletList: "글머리 목록",
      comparisonTable: "비교 표",
      metricChart: "지표 차트",
      iconCards: "아이콘 카드",
      text: "텍스트",
      cards: "카드",
      list: "목록",
      table: "표",
      chart: "차트",
      mixed: "혼합",
    };
    const patternLabel = pattern ? patternLabels[pattern] : undefined;
    const label = instruction || "AI 편집존";
    const detail = patternLabel ? `AI 구성 · ${patternLabel}` : "AI 구성 영역";
    return <Group {...common}>
      <Rect width={element.bounds.width} height={element.bounds.height} fill="rgba(124,58,237,0.08)" stroke={selected ? "#2563eb" : "#7c3aed"} strokeWidth={selected ? 2 : 1} dash={[8, 6]} />
      <Text width={element.bounds.width} height={Math.max(24, element.bounds.height - 24)} text={label} align={zone?.textAlign ?? "left"} ellipsis fill="#6d28d9" fontSize={16} fontStyle="bold" padding={8} listening={false} />
      <Text y={Math.max(24, element.bounds.height - 24)} width={element.bounds.width} height={24} text={detail} align={zone?.textAlign ?? "left"} ellipsis fill="#7c3aed" fontSize={11} padding={8} listening={false} />
    </Group>;
  }
  return <Group {...common}>
    {kind !== "placeholder" && <Rect width={element.bounds.width} height={element.bounds.height} fill="rgba(0,0,0,0.001)" />}
    {kind === "placeholder" && showPlaceholderChrome && <Rect width={element.bounds.width} height={element.bounds.height} fill="rgba(236,253,245,0.35)" stroke={outline} strokeWidth={selected ? 2 : 1} dash={[7, 5]} listening={false} />}
    <Text width={element.bounds.width} height={element.bounds.height} text={text} fill={semantic.style?.color ?? element.color} fontFamily={semantic.style?.fontFamily ?? element.fontFamily} fontSize={semantic.style?.fontSize ?? element.fontSize} fontStyle={(semantic.style?.fontWeight ?? element.fontWeight) >= 600 ? "bold" : "normal"} align={semantic.style?.textAlign} lineHeight={1.2} padding={4} verticalAlign={semantic.style?.verticalAlign || "middle"} opacity={opacity} />
    {selected && kind !== "placeholder" && <Rect width={element.bounds.width} height={element.bounds.height} stroke="#2563eb" strokeWidth={2} listening={false} />}
  </Group>;
}

export interface MasterKonvaCanvasProps {
  scene: MasterCanvasScene;
  titleLines: string[];
  body: string;
  logoUrl?: string | null;
  resolveAssetUrl?: (asset: NonNullable<MasterCanvasElement["asset"]>) => string | null | undefined;
  onTransaction: (transaction: MasterCanvasTransaction) => void;
  onCopy?: (elementIds: string[]) => void;
  onPaste?: () => void;
  canPaste?: boolean;
  onWarningChange?: (hasWarning: boolean) => void;
  onSelectionChange?: (elements: MasterCanvasElement[]) => void;
  editRequest?: { elementId: string; nonce: number } | null;
  onEditRequestConsumed?: (request: { elementId: string; nonce: number }) => void;
  onUndo?: () => void;
  onRedo?: () => void;
  /** Clears transient editor state after a history jump. */
  interactionResetNonce?: number;
  /** Renders the slide as read-only while retaining its current selection for restoration. */
  preview?: boolean;
}

/**
 * Konva interaction surface. It keeps gestures in Konva nodes until their end,
 * then emits one semantic-master transaction; Konva JSON is never persisted.
 */
export default function MasterKonvaCanvas({ scene, titleLines, body, logoUrl, resolveAssetUrl, onTransaction, onCopy, onPaste, canPaste = false, onWarningChange, onSelectionChange, editRequest, onEditRequestConsumed, onUndo, onRedo, interactionResetNonce, preview = false }: MasterKonvaCanvasProps) {
  const t = useTranslations("authoredVnext.master.studio");
  const { containerRef, dimensions, viewport, armPan, beginPan, stageProps, zoomBy, fit } = useCanvasCoreViewport({ slideWidth: scene.width, slideHeight: scene.height });
  const transformerRef = useRef<Konva.Transformer | null>(null);
  const nodesRef = useRef(new Map<string, Konva.Node>());
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const composingRef = useRef(false);
  const consumedEditRequestNonceRef = useRef<number | null>(null);
  const multiDragRef = useRef<{
    sourceId: string;
    sourceStart: { x: number; y: number };
    entries: Array<{ element: MasterCanvasElement; node: Konva.Node; start: { x: number; y: number } }>;
  } | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [guides, setGuides] = useState<CanvasCoreGuide[]>([]);
  const [showSafeArea, setShowSafeArea] = useState(true);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [inlineEditor, setInlineEditor] = useState<InlineEditor>(null);

  const visibleElements = useMemo(
    () => scene.elements
      .filter((item) => item.visible && (!preview || item.kind !== "aiEditZone"))
      .sort((left, right) => left.zIndex - right.zIndex),
    [preview, scene.elements],
  );
  const selected = useMemo(() => visibleElements.filter((item) => selectedIds.includes(item.id)), [selectedIds, visibleElements]);
  const selectedIdsKey = selectedIds.join(",");
  const canTransform = !preview && selected.length > 0 && selected.every((item) => item.editable);
  const canAlign = selected.length > 0 && selected.every((item) => item.kind !== "aiEditZone");
  const canReorder = selected.length > 0 && selected.every((item) => item.layer === "semantic");
  const warning = false;

  useEffect(() => onWarningChange?.(warning), [onWarningChange, warning]);
  useEffect(() => onSelectionChange?.(selected), [onSelectionChange, selected]);
  useEffect(() => { setSelectedIds((current) => current.filter((id) => visibleElements.some((item) => item.id === id))); }, [visibleElements]);
  useEffect(() => {
    if (!preview) return;
    setInlineEditor(null);
    setGuides([]);
  }, [preview]);
  useEffect(() => {
    if (interactionResetNonce === undefined) return;
    setInlineEditor(null);
    setSelectedIds([]);
    setGuides([]);
  }, [interactionResetNonce]);
  useEffect(() => {
    const nodes = canTransform ? selectedIds.map((id) => nodesRef.current.get(id)).filter((node): node is Konva.Node => Boolean(node)) : [];
    transformerRef.current?.nodes(nodes.length === selectedIds.length ? nodes : []);
    transformerRef.current?.getLayer()?.batchDraw();
  }, [canTransform, selectedIds, selectedIdsKey, visibleElements]);
  useEffect(() => { if (inlineEditor) window.setTimeout(() => editorRef.current?.focus(), 0); }, [inlineEditor]);

  const setNode = useCallback((id: string, node: Konva.Node | null) => { if (node) nodesRef.current.set(id, node); else nodesRef.current.delete(id); }, []);
  const select = useCallback((element: MasterCanvasElement, additive: boolean) => {
    if (preview) return;
    setInlineEditor(null);
    setSelectedIds((current) => additive ? (current.includes(element.id) ? current.filter((id) => id !== element.id) : [...current, element.id]) : [element.id]);
  }, [preview]);
  const startTextEditing = useCallback((element: MasterCanvasElement) => {
    if (preview || !element.editable || (element.kind !== "text" && element.kind !== "pageNumber")) return;
    setSelectedIds([element.id]);
    setInlineEditor({ elementId: element.id, value: element.text || "" });
  }, [preview]);
  useEffect(() => {
    if (!editRequest || consumedEditRequestNonceRef.current === editRequest.nonce) return;
    const element = visibleElements.find((item) => item.id === editRequest.elementId);
    if (!element || preview) return;
    setSelectedIds([element.id]);
    setInlineEditor(null);
    consumedEditRequestNonceRef.current = editRequest.nonce;
    onEditRequestConsumed?.(editRequest);
  }, [editRequest, onEditRequestConsumed, preview, visibleElements]);
  const geometryUpdate = useCallback((element: MasterCanvasElement, node: Konva.Node) => {
    const bounds = boundedBounds({ x: node.x(), y: node.y(), width: node.width() * node.scaleX(), height: node.height() * node.scaleY() }, scene);
    node.position({ x: bounds.x, y: bounds.y });
    node.scale({ x: 1, y: 1 });
    node.width(bounds.width);
    node.height(bounds.height);
    return { elementId: element.id, bounds, rotation: node.rotation() };
  }, [scene]);
  const commitGeometry = useCallback((entries: Array<{ element: MasterCanvasElement; node: Konva.Node }>) => {
    if (!entries.length) return;
    const updates = entries.map(({ element, node }) => geometryUpdate(element, node));
    setGuides([]);
    const zones = updates.filter((_, index) => entries[index].element.kind === "aiEditZone");
    const elements = updates.filter((_, index) => entries[index].element.kind !== "aiEditZone");
    zones.forEach((update) => onTransaction({ type: "updateAiEditZoneGeometry", zoneId: update.elementId, bounds: update.bounds }));
    if (!elements.length) return;
    onTransaction(elements.length === 1
      ? { type: "updateGeometry", ...elements[0] }
      : { type: "updateGeometryBatch", updates: elements });
  }, [geometryUpdate, onTransaction]);
  const beginDrag = useCallback((element: MasterCanvasElement, node: Konva.Node) => {
    if (!selectedIds.includes(element.id) || selectedIds.length < 2) {
      multiDragRef.current = null;
      return;
    }
    const entries = selectedIds.flatMap((id) => {
      const selectedElement = visibleElements.find((item) => item.id === id);
      const selectedNode = nodesRef.current.get(id);
      return selectedElement && selectedNode ? [{ element: selectedElement, node: selectedNode, start: { x: selectedNode.x(), y: selectedNode.y() } }] : [];
    });
    multiDragRef.current = entries.length === selectedIds.length ? { sourceId: element.id, sourceStart: { x: node.x(), y: node.y() }, entries } : null;
  }, [selectedIds, visibleElements]);
  const snapNode = useCallback((element: MasterCanvasElement, node: Konva.Node) => {
    if (!snapEnabled) return;
    const next = { x: node.x(), y: node.y() };
    const nextGuides: CanvasCoreGuide[] = [];
    const vertical = [scene.safeArea.x, scene.safeArea.x + scene.safeArea.width / 2, scene.safeArea.x + scene.safeArea.width];
    const horizontal = [scene.safeArea.y, scene.safeArea.y + scene.safeArea.height / 2, scene.safeArea.y + scene.safeArea.height];
    for (const other of visibleElements) {
      if (other.id === element.id || selectedIds.includes(other.id)) continue;
      vertical.push(other.bounds.x, other.bounds.x + other.bounds.width / 2, other.bounds.x + other.bounds.width);
      horizontal.push(other.bounds.y, other.bounds.y + other.bounds.height / 2, other.bounds.y + other.bounds.height);
    }
    const snapAxis = (value: number, candidates: number[], orientation: CanvasCoreGuide["orientation"]) => {
      const found = candidates.find((candidate) => Math.abs(value - candidate) <= SNAP_DISTANCE / Math.max(viewport.scale, 0.1));
      if (found === undefined) return value;
      nextGuides.push({ orientation, position: found, start: orientation === "vertical" ? 0 : 0, end: orientation === "vertical" ? scene.height : scene.width });
      return found - (orientation === "vertical" ? 0 : 0) + (value - value);
    };
    next.x = snapAxis(next.x, vertical, "vertical");
    next.y = snapAxis(next.y, horizontal, "horizontal");
    node.position(next);
    const multiDrag = multiDragRef.current;
    if (multiDrag?.sourceId === element.id) {
      const delta = { x: next.x - multiDrag.sourceStart.x, y: next.y - multiDrag.sourceStart.y };
      multiDrag.entries.forEach((entry) => {
        if (entry.node !== node) entry.node.position({ x: entry.start.x + delta.x, y: entry.start.y + delta.y });
      });
      transformerRef.current?.forceUpdate();
    }
    setGuides(nextGuides);
  }, [scene, selectedIds, snapEnabled, viewport.scale, visibleElements]);
  const commitInlineEditor = useCallback((currentValue?: string) => {
    if (preview || !inlineEditor) return;
    const text = currentValue ?? inlineEditor.value;
    const element = visibleElements.find((item) => item.id === inlineEditor.elementId);
    if (text !== (element?.text || "")) onTransaction({
      type: "updateText",
      elementId: inlineEditor.elementId,
      text,
    });
    setInlineEditor(null);
  }, [inlineEditor, onTransaction, preview, visibleElements]);
  const applyToSelection = useCallback((transaction: MasterCanvasTransaction) => onTransaction(transaction), [onTransaction]);

  const inlineElement = inlineEditor ? visibleElements.find((item) => item.id === inlineEditor.elementId) : null;
  const deleteSelection = selected.filter((item) => item.deletable).map((item) => item.id);
  const deleteSelected = () => {
    const zoneIds = selected.filter((item) => item.kind === "aiEditZone").map((item) => item.id);
    const elementIds = deleteSelection.filter((id) => !zoneIds.includes(id));
    if (zoneIds.length) onTransaction({ type: "deleteAiEditZones", zoneIds });
    if (elementIds.length) onTransaction({ type: "delete", elementIds });
  };
  const copySelection = selected.filter((item) => item.duplicable).map((item) => item.id);
  const editorStyle = inlineElement ? {
    left: viewport.x + inlineElement.bounds.x * viewport.scale,
    top: viewport.y + inlineElement.bounds.y * viewport.scale,
    width: inlineElement.bounds.width * viewport.scale,
    height: Math.max(28, inlineElement.bounds.height * viewport.scale),
    fontFamily: inlineElement.fontFamily,
    fontSize: inlineElement.fontSize * viewport.scale,
    fontWeight: inlineElement.fontWeight,
    color: inlineElement.color,
  } : undefined;

  return <section className="rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid="master-studio" data-preview-mode={preview ? "true" : "false"}>
    {!preview && <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 p-2 text-xs">
      <button type="button" className="rounded border px-2 py-1" aria-pressed={snapEnabled} onClick={() => setSnapEnabled((current) => !current)}>{t("snap", { state: t(snapEnabled ? "on" : "off") })}</button>
      <button type="button" className="rounded border px-2 py-1" aria-pressed={showSafeArea} onClick={() => setShowSafeArea((current) => !current)}>{t("safeArea")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canAlign} onClick={() => applyToSelection({ type: "align", elementIds: selectedIds, alignment: "left", relativeTo: "safeArea" })}>{t("alignLeft")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canAlign} onClick={() => applyToSelection({ type: "align", elementIds: selectedIds, alignment: "center", relativeTo: "safeArea" })}>{t("alignCenter")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canAlign} onClick={() => applyToSelection({ type: "align", elementIds: selectedIds, alignment: "right", relativeTo: "safeArea" })}>{t("alignRight")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canAlign} onClick={() => applyToSelection({ type: "align", elementIds: selectedIds, alignment: "top", relativeTo: "safeArea" })}>{t("alignTop")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canAlign} onClick={() => applyToSelection({ type: "align", elementIds: selectedIds, alignment: "middle", relativeTo: "safeArea" })}>{t("alignMiddle")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canAlign} onClick={() => applyToSelection({ type: "align", elementIds: selectedIds, alignment: "bottom", relativeTo: "safeArea" })}>{t("alignBottom")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canReorder} onClick={() => applyToSelection({ type: "reorder", elementIds: selectedIds, direction: "front" })}>{t("bringFront")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!canReorder} onClick={() => applyToSelection({ type: "reorder", elementIds: selectedIds, direction: "back" })}>{t("sendBack")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!copySelection.length} onClick={() => applyToSelection({ type: "duplicate", elementIds: copySelection })}>{t("duplicate")}</button>
      <button type="button" className="rounded border px-2 py-1 disabled:opacity-40" disabled={!deleteSelection.length} onClick={deleteSelected}>{t("delete")}</button>
      {selected.length === 1 && selected[0].kind === "image" && <><button type="button" className="rounded border px-2 py-1" onClick={() => onTransaction({ type: "updateImagePresentation", elementId: selected[0].id, fit: "contain" })}>{t("fit")}</button><button type="button" className="rounded border px-2 py-1" onClick={() => onTransaction({ type: "updateImagePresentation", elementId: selected[0].id, fit: "cover" })}>{t("fill")}</button></>}
    </div>}
    <div ref={containerRef} className="relative h-[72vh] min-h-[480px] w-full overflow-hidden bg-slate-800 outline-none" tabIndex={preview ? -1 : 0} role="application" aria-label={t("canvasAria")} data-testid="master-studio-canvas" data-preview-mode={preview ? "true" : "false"} data-editing-chrome={preview ? "hidden" : "visible"} data-viewport-x={viewport.x} data-viewport-y={viewport.y} data-viewport-scale={viewport.scale} onKeyDown={(event) => {
      if (preview) return;
      if (inlineEditor) return;
      const shortcut = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (shortcut && key === "z") { event.preventDefault(); setInlineEditor(null); setSelectedIds([]); setGuides([]); if (event.shiftKey) onRedo?.(); else onUndo?.(); return; }
      if (shortcut && key === "y") { event.preventDefault(); setInlineEditor(null); setSelectedIds([]); setGuides([]); onRedo?.(); return; }
      if (event.code === "Space") { event.preventDefault(); armPan(true); return; }
      if (shortcut && key === "c") { if (copySelection.length && onCopy) { event.preventDefault(); onCopy(copySelection); } return; }
      if (shortcut && key === "v") { if (canPaste && onPaste) { event.preventDefault(); onPaste(); } return; }
      if (event.key === "Enter" && selected.length === 1) { event.preventDefault(); startTextEditing(selected[0]); return; }
      if ((event.key === "Delete" || event.key === "Backspace") && deleteSelection.length) { event.preventDefault(); deleteSelected(); return; }
      const nudge = event.key === "ArrowLeft" ? [-1, 0] : event.key === "ArrowRight" ? [1, 0] : event.key === "ArrowUp" ? [0, -1] : event.key === "ArrowDown" ? [0, 1] : null;
      if (nudge && selected.length) {
        event.preventDefault();
        const amount = event.shiftKey ? 10 : 1;
        const moved = selected.map((item) => ({ elementId: item.id, kind: item.kind, bounds: boundedBounds({ ...item.bounds, x: item.bounds.x + nudge[0] * amount, y: item.bounds.y + nudge[1] * amount }, scene) }));
        const zones = moved.filter((item) => item.kind === "aiEditZone");
        const elements = moved.filter((item) => item.kind !== "aiEditZone");
        zones.forEach((item) => onTransaction({ type: "updateAiEditZoneGeometry", zoneId: item.elementId, bounds: item.bounds }));
        if (elements.length) onTransaction({ type: "updateGeometryBatch", updates: elements.map(({ elementId, bounds }) => ({ elementId, bounds })) });
      }
    }} onKeyUp={(event) => { if (!preview && event.code === "Space") armPan(false); }} onBlur={() => armPan(false)}>
      <Stage width={dimensions.width} height={dimensions.height} {...stageProps} listening={!preview} onMouseDown={(event) => { if (preview || beginPan(event)) return; const stage = event.target.getStage(); if (event.target === stage || event.target.name() === "slide-background") { setSelectedIds([]); setInlineEditor(null); } }}>
        <Layer><Group x={viewport.x} y={viewport.y} scaleX={viewport.scale} scaleY={viewport.scale}>
          <Rect name="slide-background" width={scene.width} height={scene.height} fill={scene.background} shadowColor="#020617" shadowBlur={18} shadowOpacity={0.45} />
          {!preview && showSafeArea && <Rect x={scene.safeArea.x} y={scene.safeArea.y} width={scene.safeArea.width} height={scene.safeArea.height} stroke="#60a5fa" dash={[8, 5]} listening={false} />}
          {visibleElements.map((element) => <CanvasElement key={element.id} element={element} titleLines={titleLines} body={body} placeholderLabels={{ title: t("placeholderTitle"), body: t("placeholderBody"), content: t("placeholderContent") }} logoUrl={logoUrl} resolveAssetUrl={resolveAssetUrl} imageFit={element.content?.fit || (element.kind === "image" ? "contain" : "cover")} selected={!preview && selectedIds.includes(element.id)} interactive={!preview} showPlaceholderChrome={!preview} setNode={setNode} onSelect={select} onDoubleClick={startTextEditing} onDragStart={beginDrag} onDragMove={snapNode} onDragEnd={(dragged, node) => { const multiDrag = multiDragRef.current; multiDragRef.current = null; commitGeometry(multiDrag?.sourceId === dragged.id ? multiDrag.entries : [{ element: dragged, node }]); }} />)}
          {!preview && <Transformer ref={transformerRef} rotateEnabled={canTransform && selected.length > 0 && selected.every((item) => Boolean((item as unknown as { rotatable?: boolean }).rotatable))} enabledAnchors={canTransform ? ["top-left", "top-center", "top-right", "middle-left", "middle-right", "bottom-left", "bottom-center", "bottom-right"] : []} flipEnabled={false} ignoreStroke boundBoxFunc={(oldBox, newBox) => Math.abs(newBox.width) < MIN_SIZE || Math.abs(newBox.height) < MIN_SIZE ? oldBox : newBox} onTransformEnd={() => commitGeometry(selected.flatMap((element) => { const node = nodesRef.current.get(element.id); return node ? [{ element, node }] : []; }))} />}
          {!preview && <CanvasCoreGuides guides={guides} scale={viewport.scale} />}
        </Group></Layer>
      </Stage>
      {!preview && inlineEditor && inlineElement && <textarea key={inlineEditor.elementId} ref={editorRef} data-testid="master-studio-inline-editor" aria-label={t("canvasTextAria")} className="absolute z-10 resize-none border-2 border-blue-500 bg-white/95 p-1 outline-none" style={editorStyle} defaultValue={inlineEditor.value} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setInlineEditor(null); } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !composingRef.current) { event.preventDefault(); commitInlineEditor(event.currentTarget.value); } }} onBlur={(event) => commitInlineEditor(event.currentTarget.value)} />}
      {!preview && <CanvasCoreZoomControls scale={viewport.scale} zoomBy={zoomBy} fit={fit} zoomOutLabel={t("zoomOut")} zoomInLabel={t("zoomIn")} fitLabel={t("fitSlide")} fitText={t("fit")} />}
      {preview && <div className="pointer-events-none absolute right-3 top-3 rounded bg-slate-950/75 px-2 py-1 text-xs font-medium text-white" data-testid="master-studio-preview-marker">{t("toolbar.preview")}</div>}
    </div>
    {warning && !preview && <p className="border-t border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="alert" data-testid="master-canvas-overlap-warning">{t("overlapWarning")}</p>}
  </section>;
}
