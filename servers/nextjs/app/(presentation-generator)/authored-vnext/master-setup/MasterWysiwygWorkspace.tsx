"use client";

import { useRef, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";

import type { BrandAsset } from "@/lib/authored-vnext/master/contracts";

import type {
  MasterCanvasElement,
  MasterCanvasScene,
} from "./master-canvas-adapter";
import {
  PptAnalysisReviewInspector,
  PptAnalysisReviewLeftRail,
  PptAnalysisReviewToolbar,
  type PptAnalysisReviewProps,
} from "./PptAnalysisReviewPanel";

type Transaction = { type: string; [key: string]: unknown };
type LayoutItem = {
  selector: string;
  id: string;
  name: string;
  required: boolean;
};
type SharedLogoScope = "current" | "allBody" | "selected" | "roles";
type SharedLogoAnchor = "topLeft" | "topRight" | "bottomLeft" | "bottomRight";
type LayoutRole = "cover" | "section" | "body" | "closing";

function layoutRole(
  layout: LayoutItem,
  analysisLayouts?: PptAnalysisReviewProps["layouts"],
): LayoutRole {
  const analysis = analysisLayouts?.find((candidate) => candidate.selector === layout.selector || candidate.selector === layout.id || candidate.id === layout.id);
  const analysisKind = analysis?.kind?.toLowerCase();
  if (analysisKind === "cover" || analysisKind === "section" || analysisKind === "closing") return analysisKind;
  const value = `${layout.selector} ${layout.id} ${layout.name}`.toLowerCase();
  if (/\bcover\b|표지/.test(value)) return "cover";
  if (/\bsection\b|섹션/.test(value)) return "section";
  // Imported/AI-created closing layouts are custom today; keep the mapping
  // deterministic until semantic layout-role metadata is available on v1.
  if (/\bclosing\b|\bclose\b|\boutro\b|\bend\b|마무리|끝/.test(value)) return "closing";
  return "body";
}

function isSharedLogo(element: MasterCanvasElement | null): boolean {
  return element?.kind === "image" && element.content?.role === "brand-logo" && Boolean(element.content.sharedGroupId);
}

export interface MasterWysiwygWorkspaceProps {
  activeLayout: string;
  children: ReactNode;
  layouts: LayoutItem[];
  onLayoutChange: (selector: string) => void;
  onPreviewChange: (preview: boolean) => void;
  onTransaction: (transaction: Transaction) => void;
  onUploadAsset?: (file: File) => Promise<BrandAsset>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  preview: boolean;
  scene: MasterCanvasScene;
  selected: MasterCanvasElement[];
  /** Count supplied from canonical contract, not just visible scene elements. */
  sharedLogoTargetCount?: number;
  hasBoundCanonicalLogo?: boolean;
  /** Optional deterministic source-analysis evidence. The parent persists it. */
  analysisReview?: PptAnalysisReviewProps;
}

/** Editor chrome: all persistent changes leave here as semantic transactions. */
export default function MasterWysiwygWorkspace(
  props: MasterWysiwygWorkspaceProps,
) {
  const t = useTranslations("authoredVnext.master.studio");
  const [uploadMode, setUploadMode] = useState<"replace" | "sharedLogo" | null>(
    null,
  );
  const [sharedLogoScope, setSharedLogoScope] = useState<SharedLogoScope>("current");
  const [sharedLogoAnchor, setSharedLogoAnchor] = useState<SharedLogoAnchor>("bottomRight");
  const [selectedLogoLayoutIds, setSelectedLogoLayoutIds] = useState<string[]>([]);
  const [selectedLogoRoles, setSelectedLogoRoles] = useState<LayoutRole[]>(["body"]);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const selected = props.selected;
  const selectionHasAiEditZone = selected.some((element) => element.kind === "aiEditZone");
  const one = selected.length === 1 ? selected[0] : null;
  const semantic = one as
    | (MasterCanvasElement & {
        rotation?: number;
        style?: Record<string, unknown>;
      })
    | null;
  const isAiEditZone = one?.kind === "aiEditZone";
  const aiEditZone = isAiEditZone ? one.aiEditZone : undefined;
  const selectedSharedLogo = isSharedLogo(one);
  const sharedLogoOverride = selectedSharedLogo && Boolean(one?.content?.sharedOverride);
  const sharedLogoGroupId = selectedSharedLogo ? one?.content?.sharedGroupId : undefined;
  const sharedLogoTargetIds = (() => {
    if (sharedLogoScope === "current") return [props.scene.layoutId];
    if (sharedLogoScope === "allBody") return props.layouts.filter((layout) => layoutRole(layout, props.analysisReview?.layouts) === "body").map((layout) => layout.id);
    if (sharedLogoScope === "selected") return selectedLogoLayoutIds.length ? selectedLogoLayoutIds : [props.scene.layoutId];
    return props.layouts.filter((layout) => selectedLogoRoles.includes(layoutRole(layout, props.analysisReview?.layouts))).map((layout) => layout.id);
  })();

  const emit = (transaction: Transaction) => props.onTransaction(transaction);
  const addFurniture = (
    type: "text" | "shape" | "line" | "pageNumber",
    role: "fixed-copy" | "background-decoration" | "divider" | "slide-number",
    extra: Record<string, unknown> = {},
  ) => {
    const position = 72 + (props.scene.elements.length % 6) * 20;
    const pageNumberWidth = 160;
    const pageNumberHeight = 48;
    const bounds =
      type === "pageNumber"
        ? {
            x: Math.round((props.scene.width - pageNumberWidth) / 2),
            y: Math.round(
              props.scene.safeArea.y +
                props.scene.safeArea.height -
                pageNumberHeight,
            ),
            width: pageNumberWidth,
            height: pageNumberHeight,
          }
        : {
            x: position,
            y: position,
            width: type === "line" ? 260 : 240,
            height: type === "line" ? 4 : 86,
          };
    emit({
      type: "insertElement",
      element: {
        id: `element:${crypto.randomUUID()}`,
        type,
        scope: { kind: "layout", layoutId: props.scene.layoutId },
        bounds,
        rotation: 0,
        zIndex: props.scene.elements.length + 10,
        visibility: "layoutOnly",
        editableByUser: true,
        editableByAI: false,
        locked: false,
        style:
          type === "shape"
            ? {
                shape: extra.shape || "rect",
                fill: "#dbeafe",
                stroke: "#2563eb",
              }
            : type === "pageNumber"
              ? {
                  color: "#0f172a",
                  fontSize: 28,
                  fontWeight: 700,
                  textAlign: "center",
                  verticalAlign: "middle",
                }
              : { color: "#0f172a" },
        content:
          type === "text"
            ? { role, text: "Fixed copy" }
            : type === "pageNumber"
              ? { role: "slide-number", text: "" }
              : {
                  role,
                  ...(typeof extra.content === "object" && extra.content !== null
                    ? extra.content as Record<string, unknown>
                    : {}),
                },
      },
    });
  };
  const updateGeometry = (patch: Record<string, number>) => {
    if (!one) return;
    const bounds = { ...one.bounds, ...patch };
    if (one.kind === "aiEditZone") {
      emit({ type: "updateAiEditZoneGeometry", zoneId: one.id, bounds });
      return;
    }
    emit({
      type: "updateGeometry",
      elementId: one.id,
      bounds,
      rotation: patch.rotation ?? semantic?.rotation ?? 0,
    });
  };
  const updateStyle = (style: Record<string, unknown>) =>
    one && emit({ type: "updateStyle", elementIds: [one.id], style });
  const addAiEditZone = () => {
    const safeArea = props.scene.safeArea;
    emit({
      type: "addAiEditZone",
      zone: {
        id: `ai-zone:${crypto.randomUUID()}`,
        scope: { kind: "layout", layoutId: props.scene.layoutId },
        bounds: {
          x: safeArea.x,
          y: safeArea.y,
          width: Math.min(360, safeArea.width),
          height: Math.min(92, safeArea.height),
        },
        instruction: "",
        contentType: "auto",
        pattern: "auto",
        sourcePolicy: "promptAndDocument",
        emptyBehavior: "hidden",
        density: "balanced",
        textAlign: "left",
        stylePolicy: "inheritTemplate",
      },
    });
  };
  const updateAiEditZone = (patch: Record<string, unknown>) => {
    if (!aiEditZone) return;
    emit({
      type: "updateAiEditZone",
      zoneId: aiEditZone.id,
      patch: {
        ...patch,
        // A legacy geometry-only zone receives the renderer-safe policy only
        // when its author intentionally starts configuring generation.
        ...(aiEditZone.stylePolicy ? {} : { stylePolicy: "inheritTemplate" }),
      },
    });
  };
  const splitAiEditZoneByLayout = () => {
    if (!aiEditZone || aiEditZone.scope.kind !== "common") return;
    emit({
      type: "splitAiEditZoneByLayout",
      zoneId: aiEditZone.id,
      layoutIds: props.layouts.map((layout) => layout.id),
      idFactory: () => `ai-zone:${crypto.randomUUID()}`,
    });
  };

  const fitAiEditZoneToSafeArea = () => {
    if (!aiEditZone) return;
    const safeArea = props.scene.safeArea;
    const width = Math.min(aiEditZone.bounds.width, safeArea.width);
    const height = Math.min(aiEditZone.bounds.height, safeArea.height);
    emit({
      type: "updateAiEditZoneGeometry",
      zoneId: aiEditZone.id,
      bounds: {
        x: Math.min(
          Math.max(aiEditZone.bounds.x, safeArea.x),
          safeArea.x + safeArea.width - width,
        ),
        y: Math.min(
          Math.max(aiEditZone.bounds.y, safeArea.y),
          safeArea.y + safeArea.height - height,
        ),
        width,
        height,
      },
    });
  };
  const requestUpload = (mode: "replace") => {
    setUploadMode(mode);
    inputRef.current?.click();
  };
  const upload = async (file: File) => {
    if (!props.onUploadAsset || !uploadMode) return;
    const asset = await props.onUploadAsset(file);
    if (uploadMode === "sharedLogo") {
      emit({
        type: "insertSharedLogo",
        asset,
        layoutIds: sharedLogoTargetIds,
        anchor: sharedLogoAnchor,
        groupId: `shared-logo:${crypto.randomUUID()}`,
      });
    } else if (uploadMode === "replace" && one) {
      emit({
        type: "updateImagePresentation",
        elementId: one.id,
        asset,
        mediaType: "image/png",
      });
    } else {
      throw new Error("Only an existing logo furniture element can be replaced");
    }
    setUploadMode(null);
  };
  const crop = (field: "x" | "y" | "width" | "height", value: number) =>
    one &&
    emit({
      type: "updateImagePresentation",
      elementId: one.id,
      crop: {
        x: semantic?.content?.crop?.x ?? 0,
        y: semantic?.content?.crop?.y ?? 0,
        width: semantic?.content?.crop?.width ?? 1,
        height: semantic?.content?.crop?.height ?? 1,
        [field]: value,
      },
    });

  return (
    <section
      className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-100 shadow-sm"
      data-testid="master-wysiwyg-workspace"
    >
      <input
        ref={inputRef}
        className="sr-only"
        accept="image/png,image/jpeg,image/svg+xml"
        type="file"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) void upload(file);
        }}
      />
      <header className="flex flex-wrap items-center gap-1 border-b border-slate-200 bg-white px-3 py-2 text-xs">
        <div className={props.preview ? "hidden" : "contents"}>
        <button
          type="button"
          className="rounded border px-2 py-1"
          onClick={() => addFurniture("text", "fixed-copy")}
        >
          Fixed copy
        </button>
        <button
          type="button"
          className="rounded border border-violet-300 bg-violet-50 px-2 py-1 text-violet-900"
          onClick={addAiEditZone}
        >
          AI 편집존
        </button>
        <button
          type="button"
          className="rounded border border-blue-300 bg-blue-50 px-2 py-1 text-blue-950"
          onClick={() => {
            setUploadMode("sharedLogo");
            inputRef.current?.click();
          }}
        >
          {t("toolbar.companyLogo")}
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1"
          onClick={() => addFurniture("shape", "background-decoration", { shape: "rect" })}
        >
          Background decoration
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1"
          onClick={() => addFurniture("shape", "background-decoration", { shape: "ellipse" })}
        >
          Background decoration (circle)
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1"
          onClick={() => addFurniture("line", "divider")}
        >
          {t("toolbar.line")}
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1"
          onClick={() => addFurniture("pageNumber", "slide-number")}
        >
          {t("toolbar.pageNumber")}
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1 disabled:opacity-40"
          disabled={!selected.length || selectionHasAiEditZone}
          onClick={() =>
            emit({
              type: "align",
              elementIds: selected.map((item) => item.id),
              alignment: "left",
              relativeTo: "safeArea",
            })
          }
        >
          {t("alignLeft")}
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1 disabled:opacity-40"
          disabled={!selected.length || selectionHasAiEditZone}
          onClick={() =>
            emit({
              type: "align",
              elementIds: selected.map((item) => item.id),
              alignment: "center",
              relativeTo: "safeArea",
            })
          }
        >
          {t("alignCenter")}
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1 disabled:opacity-40"
          disabled={!selected.length || selectionHasAiEditZone}
          onClick={() =>
            emit({
              type: "reorder",
              elementIds: selected.map((item) => item.id),
              direction: "front",
            })
          }
        >
          {t("bringFront")}
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1 disabled:opacity-40"
          disabled={!selected.length || selectionHasAiEditZone}
          onClick={() =>
            emit({
              type: "reorder",
              elementIds: selected.map((item) => item.id),
              direction: "back",
            })
          }
        >
          {t("sendBack")}
        </button>
        </div>
        <span className="ml-auto flex gap-1">
          {!props.preview && (
            <>
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-40"
            disabled={!props.canUndo}
            onClick={props.onUndo}
          >
            {t("undo")}
          </button>
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-40"
            disabled={!props.canRedo}
            onClick={props.onRedo}
          >
            {t("redo")}
          </button>
            </>
          )}
          <button
            type="button"
            aria-pressed={props.preview}
            className={`rounded border px-2 py-1 ${props.preview ? "border-blue-700 bg-blue-700 text-white" : ""}`}
            data-testid="master-preview-toggle"
            onClick={() => props.onPreviewChange(!props.preview)}
          >
            {t("toolbar.preview")}
          </button>
        </span>
        {props.analysisReview ? <PptAnalysisReviewToolbar review={props.analysisReview} /> : null}
      </header>
      {!props.preview && (
        <div className="flex flex-wrap items-end gap-2 border-b border-slate-200 bg-blue-50/60 px-3 py-2 text-xs" data-testid="shared-logo-scope">
          <label className="grid gap-1">
            <span className="font-medium">{t("sharedLogo.scope")}</span>
            <select
              className="rounded border border-blue-200 bg-white px-2 py-1"
              value={sharedLogoScope}
              onChange={(event) => setSharedLogoScope(event.target.value as SharedLogoScope)}
            >
              <option value="current">{t("sharedLogo.current")}</option>
              <option value="allBody">{t("sharedLogo.allBody")}</option>
              <option value="selected">{t("sharedLogo.selected")}</option>
              <option value="roles">{t("sharedLogo.roles")}</option>
            </select>
          </label>
          <label className="grid gap-1">
            <span className="font-medium">{t("sharedLogo.anchor")}</span>
            <select
              className="rounded border border-blue-200 bg-white px-2 py-1"
              value={sharedLogoAnchor}
              onChange={(event) => setSharedLogoAnchor(event.target.value as SharedLogoAnchor)}
            >
              {(["topLeft", "topRight", "bottomLeft", "bottomRight"] as const).map((anchor) => (
                <option key={anchor} value={anchor}>{t(`sharedLogo.${anchor}`)}</option>
              ))}
            </select>
          </label>
          {sharedLogoScope === "selected" && (
            <div className="flex max-w-full flex-wrap gap-x-2 gap-y-1" aria-label={t("sharedLogo.selected")}>
              {props.layouts.map((layout) => {
                const checked = selectedLogoLayoutIds.length
                  ? selectedLogoLayoutIds.includes(layout.id)
                  : layout.id === props.scene.layoutId;
                return <label className="inline-flex items-center gap-1" key={layout.id}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(event) => setSelectedLogoLayoutIds((current) => {
                      const base = current.length ? current : [props.scene.layoutId];
                      return event.target.checked
                        ? [...new Set([...base, layout.id])]
                        : base.filter((id) => id !== layout.id);
                    })}
                  />
                  {layout.name}
                </label>;
              })}
            </div>
          )}
          {sharedLogoScope === "roles" && (
            <div className="flex flex-wrap gap-2" aria-label={t("sharedLogo.roles")}>
              {(["cover", "section", "body", "closing"] as const).map((role) => (
                <label className="inline-flex items-center gap-1" key={role}>
                  <input
                    type="checkbox"
                    checked={selectedLogoRoles.includes(role)}
                    onChange={(event) => setSelectedLogoRoles((current) => event.target.checked
                      ? [...new Set([...current, role])]
                      : current.filter((candidate) => candidate !== role))}
                  />
                  {t(`sharedLogo.${role}`)}
                </label>
              ))}
            </div>
          )}
          <span className="text-slate-600">{t("sharedLogo.targetCount", { count: sharedLogoTargetIds.length })}</span>
          {props.hasBoundCanonicalLogo && <span className="max-w-xs text-amber-800">{t("sharedLogo.replacesLegacy")}</span>}
          <button
            type="button"
            className="rounded border border-blue-600 bg-blue-600 px-2 py-1 text-white disabled:opacity-40"
            disabled={!props.onUploadAsset || sharedLogoTargetIds.length === 0}
            onClick={() => {
              setUploadMode("sharedLogo");
              inputRef.current?.click();
            }}
          >
            {t("sharedLogo.insert")}
          </button>
        </div>
      )}
      <div className={props.preview ? "grid min-h-[76vh] grid-cols-1" : "grid min-h-[76vh] grid-cols-[180px_minmax(0,1fr)] xl:grid-cols-[205px_minmax(0,1fr)_250px]"}>
        <aside className={props.preview ? "hidden" : "border-r border-slate-200 bg-white p-3"}>
          {props.analysisReview ? (
            <PptAnalysisReviewLeftRail
              activeLayoutId={props.activeLayout}
              review={props.analysisReview}
              onSelectLayout={(candidate) => {
                props.onLayoutChange(candidate.selector || candidate.id);
                props.analysisReview?.onSelectLayout?.(candidate.id);
              }}
            />
          ) : null}
          <h3 className="mb-2 font-semibold">{t("layouts")}</h3>
          <nav className="space-y-1" aria-label={t("layouts")}>
            {props.layouts.map((layout) => (
              <div className="flex" key={layout.selector}>
                <button
                  type="button"
                  aria-current={
                    props.activeLayout === layout.selector ? "page" : undefined
                  }
                  className={`min-w-0 flex-1 rounded px-2 py-2 text-left ${props.activeLayout === layout.selector ? "bg-blue-600 text-white" : "hover:bg-slate-100"}`}
                  onClick={() => {
                    props.onLayoutChange(layout.selector);
                    props.analysisReview?.onSelectLayout?.(layout.id);
                  }}
                >
                  <span className="block truncate font-medium">
                    {layout.name}
                  </span>
                  <span className="block truncate text-[10px] opacity-70">
                    {layout.id}
                  </span>
                </button>
              </div>
            ))}
          </nav>
        </aside>
        <main
          className={`relative min-w-0 bg-slate-800 ${props.preview ? "col-span-full p-0" : "p-2"}`}
          data-preview-mode={props.preview ? "true" : "false"}
          data-testid="master-wysiwyg-canvas-shell"
        >
          {props.children}
          {props.analysisReview?.overlay && props.analysisReview.sourceOverlay ? (
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-slate-950/10 opacity-50"
              data-testid="ppt-analysis-source-overlay"
            >
              {props.analysisReview.sourceOverlay}
            </div>
          ) : null}
          {props.preview && (
            <div
              className="pointer-events-none absolute right-4 top-4 z-20 rounded-full bg-slate-950/80 px-3 py-1 text-xs font-semibold text-white shadow"
              data-testid="master-preview-indicator"
            >
              {t("toolbar.preview")}
            </div>
          )}
        </main>
        <aside className={props.preview ? "hidden" : "col-span-2 border-t border-slate-200 bg-white p-3 xl:col-span-1 xl:border-l xl:border-t-0"}>
          {props.analysisReview ? <PptAnalysisReviewInspector activeLayoutId={props.activeLayout} review={props.analysisReview} /> : null}
          <h3 className="font-semibold">{t("properties")}</h3>
          {!one ? (
            <p className="mt-2 text-xs text-slate-500">{t("select")}</p>
          ) : (
            <div className="mt-3 space-y-3 text-xs">
              <div className="rounded bg-slate-100 p-2">
                <strong>{t("selected", { name: one.role })}</strong>
                <p className="mt-1 text-slate-600">
                  {one.ownership.locked
                    ? t("semantics.locked")
                    : one.ownership.modelWritable
                      ? t("semantics.aiEditable")
                      : t("semantics.userEditable")}
                </p>
              </div>
              {selectedSharedLogo && (
                <fieldset className="space-y-2 rounded border border-blue-200 bg-blue-50 p-2" data-testid="shared-logo-inspector">
                  <legend className="px-1 font-semibold text-blue-950">{t("sharedLogo.title")}</legend>
                  <p className="text-blue-900">
                    {sharedLogoOverride
                      ? t("sharedLogo.overridden")
                      : t("sharedLogo.syncing", { count: props.sharedLogoTargetCount ?? 1 })}
                  </p>
                  <p className="text-slate-600">{t("sharedLogo.targetCount", { count: props.sharedLogoTargetCount ?? 1 })}</p>
                  {sharedLogoOverride ? (
                    <button
                      type="button"
                      className="rounded border border-blue-300 bg-white px-2 py-1"
                      onClick={() => emit({ type: "restoreSharedLogo", elementId: one.id })}
                    >
                      {t("sharedLogo.restore")}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="rounded border border-blue-300 bg-white px-2 py-1"
                      onClick={() => emit({ type: "setSharedLogoOverride", elementId: one.id })}
                    >
                      {t("sharedLogo.override")}
                    </button>
                  )}
                  {sharedLogoGroupId && <input aria-label="Shared logo group" readOnly className="w-full rounded border border-blue-100 bg-white px-1 text-[10px] text-slate-500" value={sharedLogoGroupId} />}
                </fieldset>
              )}
              <fieldset className="grid grid-cols-2 gap-2">
                <legend className="mb-1 font-medium">
                  {t("propertyGroups.transform")}
                </legend>
                {(["x", "y", "width", "height"] as const).map((field) => (
                  <label key={field}>
                    {field.toUpperCase()}
                    <input
                      className="mt-0.5 w-full rounded border px-1 py-1"
                      type="number"
                      value={Math.round(one.bounds[field])}
                      onChange={(event) =>
                        updateGeometry({ [field]: Number(event.target.value) })
                      }
                    />
                  </label>
                ))}
                {!isAiEditZone && <label>
                  deg
                  <input
                    className="mt-0.5 w-full rounded border px-1 py-1"
                    type="number"
                    value={semantic?.rotation ?? 0}
                    onChange={(event) =>
                      updateGeometry({ rotation: Number(event.target.value) })
                    }
                  />
                </label>}
              </fieldset>
              {isAiEditZone && aiEditZone && (
                <fieldset className="space-y-2 rounded border border-violet-200 bg-violet-50 p-2" data-testid="ai-edit-zone-inspector">
                  <legend className="px-1 font-semibold text-violet-950">AI 편집존 설정</legend>
                  <p className="text-violet-900">선택한 템플릿의 색상·글꼴·간격·카드 문법을 상속해 이 영역 안에만 생성합니다.</p>
                  {aiEditZone.scope.kind === "common" && (
                    <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-2 text-amber-950">
                      <p>현재 영역은 모든 레이아웃이 공유하므로 한 곳을 수정하면 표지·섹션·본문에 동일하게 반영됩니다.</p>
                      <button
                        type="button"
                        data-testid="split-ai-edit-zone-by-layout"
                        className="rounded border border-amber-400 bg-white px-2 py-1 font-medium"
                        onClick={splitAiEditZoneByLayout}
                      >
                        레이아웃별 독립 영역으로 분리
                      </button>
                    </div>
                  )}
                  <label className="block">
                    생성 지시어
                    <textarea
                      aria-label="AI 편집존 생성 지시어"
                      className="mt-1 min-h-20 w-full rounded border border-violet-200 bg-white p-1"
                      maxLength={4000}
                      placeholder="예: 핵심 요구사항을 3개의 번호 카드로 구성"
                      value={aiEditZone.instruction ?? ""}
                      onChange={(event) => updateAiEditZone({ instruction: event.target.value })}
                    />
                    <span className="text-[10px] text-slate-500">비워 두면 기존 예약 영역처럼 유지됩니다.</span>
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <label>
                      콘텐츠 유형
                      <select
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        value={aiEditZone.contentType ?? "auto"}
                        onChange={(event) => updateAiEditZone({ contentType: event.target.value })}
                      >
                        <option value="auto">자동</option>
                        <option value="text">텍스트</option>
                        <option value="cards">카드</option>
                        <option value="list">목록</option>
                        <option value="table">표</option>
                        <option value="chart">차트</option>
                        <option value="mixed">혼합</option>
                      </select>
                    </label>
                    <label>
                      구성 패턴
                      <select
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        value={aiEditZone.pattern ?? "auto"}
                        onChange={(event) => updateAiEditZone({ pattern: event.target.value })}
                      >
                        <option value="auto">자동</option>
                        <option value="numberedCards">번호 카드</option>
                        <option value="bulletList">글머리 목록</option>
                        <option value="comparisonTable">비교 표</option>
                        <option value="metricChart">지표 차트</option>
                        <option value="iconCards">아이콘 카드</option>
                      </select>
                    </label>
                    <label>
                      근거 사용
                      <select
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        value={aiEditZone.sourcePolicy ?? "promptAndDocument"}
                        onChange={(event) => updateAiEditZone({ sourcePolicy: event.target.value })}
                      >
                        <option value="documentOnly">문서 근거만</option>
                        <option value="promptAndDocument">지시어와 문서</option>
                        <option value="promptOnly">지시어만</option>
                      </select>
                    </label>
                    <label>
                      밀도
                      <select
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        value={aiEditZone.density ?? "balanced"}
                        onChange={(event) => updateAiEditZone({ density: event.target.value })}
                      >
                        <option value="sparse">여백 우선</option>
                        <option value="balanced">균형</option>
                        <option value="dense">정보 밀도 높음</option>
                      </select>
                    </label>
                    <label>
                      비어 있을 때
                      <select
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        value={aiEditZone.emptyBehavior ?? "hidden"}
                        onChange={(event) => updateAiEditZone({
                          emptyBehavior: event.target.value,
                          ...(event.target.value === "fallback" && !aiEditZone.fallbackText?.trim()
                            ? { fallbackText: "내용 없음" }
                            : event.target.value !== "fallback"
                              ? { fallbackText: null }
                              : {}),
                        })}
                      >
                        <option value="hidden">숨김</option>
                        <option value="blank">빈 영역 유지</option>
                        <option value="fallback">대체 문구</option>
                      </select>
                    </label>
                    <label>
                      스타일
                      <input
                        className="mt-0.5 w-full rounded border bg-slate-100 px-1 py-1 text-slate-600"
                        readOnly
                        value="템플릿 스타일 상속"
                      />
                    </label>
                    <label>
                      텍스트 정렬
                      <select
                        aria-label="AI 편집존 텍스트 정렬"
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        value={aiEditZone.textAlign ?? "inherit"}
                        onChange={(event) => updateAiEditZone({
                          textAlign: event.target.value === "inherit" ? null : event.target.value,
                        })}
                      >
                        <option value="inherit">템플릿 상속</option>
                        <option value="left">왼쪽</option>
                        <option value="center">가운데</option>
                        <option value="right">오른쪽</option>
                      </select>
                    </label>
                    <label>
                      최대 글자 수
                      <input
                        aria-label="AI 편집존 최대 글자 수"
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        min="1"
                        max="10000"
                        type="number"
                        value={aiEditZone.maxChars ?? ""}
                        onChange={(event) => updateAiEditZone({
                          maxChars: event.target.value === "" ? null : Number(event.target.value),
                        })}
                      />
                    </label>
                    <label>
                      최대 항목 수
                      <input
                        aria-label="AI 편집존 최대 항목 수"
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        min="1"
                        max="24"
                        type="number"
                        value={aiEditZone.maxItems ?? ""}
                        onChange={(event) => updateAiEditZone({
                          maxItems: event.target.value === "" ? null : Number(event.target.value),
                        })}
                      />
                    </label>
                  </div>
                  {aiEditZone.emptyBehavior === "fallback" && (
                    <label className="block">
                      대체 문구
                      <input
                        aria-label="AI 편집존 대체 문구"
                        className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                        maxLength={10000}
                        value={aiEditZone.fallbackText ?? ""}
                        onChange={(event) => updateAiEditZone({ fallbackText: event.target.value })}
                      />
                    </label>
                  )}
                  <label className="block">
                    적용 범위
                    <select
                      className="mt-0.5 w-full rounded border bg-white px-1 py-1"
                      disabled
                      value="layout"
                    >
                      <option value="layout">현재 레이아웃만</option>
                    </select>
                    <span className="text-[10px] text-slate-500">AI 편집존은 레이아웃별로 독립 저장됩니다.</span>
                  </label>
                  <button
                    type="button"
                    className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-slate-700"
                    onClick={fitAiEditZoneToSafeArea}
                  >
                    회사 표준 안전 영역 안으로 맞추기
                  </button>
                </fieldset>
              )}
              {((one.kind === "text" && !isAiEditZone) ||
                one.kind === "pageNumber") && (
                <label className="block">
                  {t("text")}
                  <textarea
                    className="mt-1 min-h-16 w-full rounded border p-1"
                    value={one.text || ""}
                    onChange={(event) =>
                      emit({
                        type: "updateContent",
                        elementId: one.id,
                        content: { text: event.target.value },
                      })
                    }
                  />
                </label>
              )}
              {!isAiEditZone && <fieldset className="grid grid-cols-2 gap-2">
                <legend className="mb-1 font-medium">
                  {t("propertyGroups.appearance")}
                </legend>
                <label>
                  {t("color")}
                  <input
                    className="ml-1 h-6 align-middle"
                    type="color"
                    value={
                      (semantic?.style?.color as string | undefined) ||
                      one.color ||
                      "#111827"
                    }
                    onChange={(event) =>
                      updateStyle({ color: event.target.value })
                    }
                  />
                </label>
                <label>
                  {t("toolbar.opacity")}
                  <input
                    className="mt-1 w-full"
                    min="0"
                    max="100"
                    type="range"
                    value={Math.round(
                      Number(semantic?.style?.opacity ?? 1) * 100,
                    )}
                    onChange={(event) =>
                      updateStyle({ opacity: Number(event.target.value) / 100 })
                    }
                  />
                </label>
                <label>
                  {t("propertyGroups.fill")}
                  <input
                    className="ml-1 h-6 align-middle"
                    type="color"
                    value={
                      (semantic?.style?.fill as string | undefined) ||
                      one.fill ||
                      "#ffffff"
                    }
                    onChange={(event) =>
                      updateStyle({ fill: event.target.value })
                    }
                  />
                </label>
                <label>
                  {t("propertyGroups.stroke")}
                  <input
                    className="ml-1 h-6 align-middle"
                    type="color"
                    value={
                      (semantic?.style?.stroke as string | undefined) ||
                      one.stroke ||
                      "#000000"
                    }
                    onChange={(event) =>
                      updateStyle({ stroke: event.target.value })
                    }
                  />
                </label>
              </fieldset>}
              {one.kind === "image" && (
                <fieldset>
                  <legend className="mb-1 font-medium">
                    {t("propertyGroups.image")}
                  </legend>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      className="rounded border px-2 py-1"
                      onClick={() =>
                        emit({
                          type: "updateImagePresentation",
                          elementId: one.id,
                          fit: "contain",
                        })
                      }
                    >
                      {t("fit")}
                    </button>
                    <button
                      type="button"
                      className="rounded border px-2 py-1"
                      onClick={() =>
                        emit({
                          type: "updateImagePresentation",
                          elementId: one.id,
                          fit: "cover",
                        })
                      }
                    >
                      {t("fill")}
                    </button>
                    {!selectedSharedLogo && <button
                      type="button"
                      className="rounded border px-2 py-1"
                      onClick={() => requestUpload("replace")}
                    >
                      {t("replaceImage")}
                    </button>}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-1">
                    {(["x", "y", "width", "height"] as const).map((field) => (
                      <label key={field}>
                        {field}
                        <input
                          className="ml-1 w-12 rounded border"
                          max="1"
                          min="0"
                          step="0.01"
                          type="number"
                          value={Number(
                            semantic?.content?.crop?.[field] ??
                              (field === "width" || field === "height" ? 1 : 0),
                          )}
                          onChange={(event) =>
                            crop(field, Number(event.target.value))
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <p className="mt-1 text-slate-500">{t("cropHelp")}</p>
                </fieldset>
              )}
              {!selectedSharedLogo && !isAiEditZone && <fieldset>
                <legend className="mb-1 font-medium">
                  {t("propertyGroups.visibility")}
                </legend>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className="rounded border px-2 py-1"
                    onClick={() =>
                      emit({
                        type: "updateScope",
                        elementIds: [one.id],
                        scope: { kind: "common" },
                      })
                    }
                  >
                    {t("sharedMaster")}
                  </button>
                  <button
                    type="button"
                    className="rounded border px-2 py-1"
                    onClick={() =>
                      emit({
                        type: "updateScope",
                        elementIds: [one.id],
                        scope: {
                          kind: "layout",
                          layoutId: props.scene.layoutId,
                        },
                      })
                    }
                  >
                    {t("semantics.layoutOnly")}
                  </button>
                </div>
              </fieldset>}
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
