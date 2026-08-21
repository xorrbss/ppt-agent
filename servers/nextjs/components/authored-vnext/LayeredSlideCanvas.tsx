"use client";

import type { CSSProperties } from "react";
import { useTranslations } from "next-intl";

import { CompositionSafeTextEditor } from "./CompositionSafeTextEditor";
import type {
  ActiveSlideScene,
  BodyLayerElement,
  ElementBounds,
  SlideOutlineItem,
  WorkbenchEntityId,
  WorkbenchTypedCommand,
} from "./types";

export interface LayeredSlideCanvasProps {
  scene: ActiveSlideScene;
  selectedElementId: WorkbenchEntityId | null;
  disabled?: boolean;
  createOperationId: () => WorkbenchEntityId;
  onSelectElement: (elementId: WorkbenchEntityId) => void;
  onCommand: (command: WorkbenchTypedCommand) => void;
}

function percentBounds(bounds: ElementBounds): CSSProperties {
  return {
    height: `${bounds.height * 100}%`,
    left: `${bounds.x * 100}%`,
    top: `${bounds.y * 100}%`,
    transform: bounds.rotation ? `rotate(${bounds.rotation}deg)` : undefined,
    width: `${bounds.width * 100}%`,
  };
}

function outlineItems(scene: ActiveSlideScene): SlideOutlineItem[] {
  return [
    ...scene.masterElements.map((element) => ({
      id: element.id,
      kind: element.kind,
      label: element.label,
      layer: "master" as const,
      text: "text" in element ? element.text : undefined,
    })),
    ...scene.bodyElements.map((element) => ({
      id: element.id,
      kind: element.kind,
      label: element.label,
      layer: "body" as const,
      text: element.kind === "text" ? element.text : undefined,
    })),
  ];
}

export function ScreenReaderSlideOutline({ scene }: { scene: ActiveSlideScene }) {
  const t = useTranslations("authoredVnext.workbench.canvas");
  return (
    <section aria-label={t("outlineAria", { title: scene.title })} className="sr-only">
      <h2>{scene.title}</h2>
      <p>{t("outlineDescription")}</p>
      <ol>
        {outlineItems(scene).map((item) => (
          <li key={`${item.layer}:${item.id}`}>
            {item.layer === "master" ? t("lockedMaster") : t("editableBody")}: {item.label}
            {item.text ? `, ${item.text}` : ""}
          </li>
        ))}
      </ol>
    </section>
  );
}

function bodyFallback(element: BodyLayerElement) {
  if (element.kind === "asset") {
    return element.previewUrl ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img alt={element.alt} className={`h-full w-full ${element.crop === "contain" ? "object-contain" : "object-cover"}`} data-asset-digest={element.assetDigest} src={element.previewUrl} />
    ) : (
      <span className="grid h-full place-items-center bg-amber-50 px-2 text-center text-[10px] text-amber-900" data-asset-digest={element.assetDigest}>
        {element.previewUnavailableReason ?? "authored_vnext_asset_preview_unavailable"}: {element.alt}
      </span>
    );
  }
  if (element.kind === "layout") {
    return <span className="grid h-full place-items-center bg-blue-50 text-xs font-medium text-blue-800">{element.name}</span>;
  }
  if (element.kind === "shape") {
    const rounded = element.shape === "roundRect";
    const circle = element.shape === "ellipse";
    const line = element.shape === "line";
    return (
      <span
        aria-label={element.label}
        className={`block h-full w-full ${rounded ? "rounded-xl" : circle ? "rounded-full" : ""} ${line ? "h-px self-center" : ""}`}
        style={{
          backgroundColor: element.style?.backgroundColor,
          border: line ? undefined : `1px solid ${element.style?.borderColor ?? "#64748b"}`,
          borderTop: line ? `2px solid ${element.style?.borderColor ?? "#64748b"}` : undefined,
        }}
      />
    );
  }
  if (element.kind === "table") {
    const payload = element.dataPayload;
    const sourceRows = Array.isArray(payload?.cells) ? payload.cells : [];
    const rows = sourceRows
      .filter((row): row is unknown[] => Array.isArray(row))
      .slice(0, 5);
    const columns = rows[0]?.length ?? 0;
    if (columns === 0 || rows.length === 0 || rows.some((row) => row.length !== columns)) {
      return <span className="grid h-full w-full place-items-center border border-dashed border-amber-500 bg-amber-50 px-2 text-center text-[10px] font-medium text-amber-900">{element.capabilityMessage}</span>;
    }
    return <table aria-label={element.label} className="h-full w-full table-fixed border-collapse text-[8px] text-slate-800"><tbody>{rows.map((row, rowIndex) => <tr className={rowIndex === 0 ? "bg-slate-100 font-semibold" : ""} key={rowIndex}>{row.map((cell, columnIndex) => <td className="truncate border border-slate-200 px-1" key={columnIndex}>{String(cell ?? "")}</td>)}</tr>)}</tbody></table>;
  }
  if (element.kind === "chart") {
    const categories = Array.isArray(element.dataPayload?.categories)
      ? element.dataPayload.categories.filter((value): value is string => typeof value === "string").slice(0, 8)
      : [];
    const series = Array.isArray(element.dataPayload?.series)
      ? element.dataPayload.series.filter((entry): entry is { name: string; values: number[] } => Boolean(entry) && typeof entry === "object" && typeof (entry as { name?: unknown }).name === "string" && Array.isArray((entry as { values?: unknown }).values) && (entry as { values: unknown[] }).values.every((value) => typeof value === "number" && Number.isFinite(value))).slice(0, 4)
      : [];
    if (categories.length === 0 || series.length === 0 || series.some((entry) => entry.values.length !== categories.length)) return <span className="grid h-full w-full place-items-center border border-dashed border-amber-500 bg-amber-50 px-2 text-center text-[10px] font-medium text-amber-900">{element.capabilityMessage}</span>;
    const chartType = element.chartType ?? "column";
    const colors = ["#2563eb", "#0f766e", "#ea580c", "#7c3aed"];
    if (chartType === "pie" || chartType === "donut") {
      if (series.length !== 1) return <span className="grid h-full w-full place-items-center border border-dashed border-amber-500 bg-amber-50 px-2 text-center text-[10px] font-medium text-amber-900">{element.capabilityMessage}</span>;
      const total = series[0].values.reduce((sum, value) => sum + Math.max(0, value), 0);
      if (total <= 0) return <span className="grid h-full w-full place-items-center border border-dashed border-amber-500 bg-amber-50 px-2 text-center text-[10px] font-medium text-amber-900">{element.capabilityMessage}</span>;
      let cursor = 0;
      const gradient = series[0].values.map((value, index) => { const next = cursor + (Math.max(0, value) / total) * 360; const stop = `${colors[index % colors.length]} ${cursor}deg ${next}deg`; cursor = next; return stop; }).join(", ");
      return <div aria-label={element.accessibilitySummary ?? element.label} className="grid h-full w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-1 bg-white p-1" role="img"><span className={`aspect-square w-full rounded-full ${chartType === "donut" ? "ring-[6px] ring-white" : ""}`} style={{ background: `conic-gradient(${gradient})` }} /> <span className="space-y-0.5 text-[7px] text-slate-700">{categories.map((category, index) => <span className="block truncate" key={category}><i className="mr-0.5 inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: colors[index % colors.length] }} />{category}</span>)}</span></div>;
    }
    const values = series.flatMap((entry) => entry.values);
    const maximum = Math.max(...values, 1);
    if (chartType === "line" || chartType === "area") return <svg aria-label={element.accessibilitySummary ?? element.label} className="h-full w-full bg-white" role="img" viewBox="0 0 100 60">{series.map((entry, seriesIndex) => { const points = entry.values.map((value, index) => `${8 + (84 * index) / Math.max(1, categories.length - 1)},${54 - (Math.max(0, value) / maximum) * 46}`).join(" "); return chartType === "area" ? <polygon fill={`${colors[seriesIndex]}33`} key={entry.name} points={`8,54 ${points} 92,54`} stroke={colors[seriesIndex]} strokeWidth="1.5" /> : <polyline fill="none" key={entry.name} points={points} stroke={colors[seriesIndex]} strokeWidth="2" />; })}</svg>;
    const columns = categories.length * series.length;
    return <svg aria-label={element.accessibilitySummary ?? element.label} className="h-full w-full bg-white" role="img" viewBox="0 0 100 60">{series.flatMap((entry, seriesIndex) => entry.values.map((value, index) => { const slot = 84 / columns; const position = (index * series.length) + seriesIndex; const magnitude = Math.max(0, value) / maximum; return chartType === "bar" ? <rect fill={colors[seriesIndex]} height="3" key={`${entry.name}-${index}`} width={Math.max(2, magnitude * 76)} x="10" y={5 + position * Math.max(4, 48 / columns)} /> : <rect fill={colors[seriesIndex]} height={Math.max(2, magnitude * 46)} key={`${entry.name}-${index}`} width={Math.max(2, slot - 1)} x={8 + position * slot} y={54 - Math.max(2, magnitude * 46)} />; }))}</svg>;
  }
  if (element.kind === "connector" || element.kind === "group") {
    return (
      <span className="grid h-full w-full place-items-center border border-dashed border-amber-500 bg-amber-50 px-2 text-center text-[10px] font-medium text-amber-900">
        {element.capabilityMessage}
      </span>
    );
  }
  return null;
}

export function LayeredSlideCanvas({
  scene,
  selectedElementId,
  disabled = false,
  createOperationId,
  onSelectElement,
  onCommand,
}: LayeredSlideCanvasProps) {
  const t = useTranslations("authoredVnext.workbench.canvas");
  return (
    <section aria-label={t("activeAria")} className="flex min-h-0 flex-1 flex-col">
      <ScreenReaderSlideOutline scene={scene} />
      <div className="flex items-center justify-between px-4 py-2 text-xs text-slate-600">
        <span className="font-medium text-slate-800">{scene.title}</span>
        <span>{t("revision", { master: scene.masterRevisionLabel, revision: scene.revision })}</span>
      </div>
      <nav aria-label={`${scene.title} objects`} className="flex gap-1 overflow-x-auto border-y border-slate-200 bg-white px-4 py-1.5">
        {scene.bodyElements.map((element) => (
          <button
            aria-pressed={element.id === selectedElementId}
            className={`shrink-0 rounded px-2 py-1 text-[11px] font-medium ${element.id === selectedElementId ? "bg-blue-700 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            data-testid="authored-scene-object-select"
            disabled={disabled}
            key={element.id}
            onClick={() => onSelectElement(element.id)}
            type="button"
          >
            {element.label}
          </button>
        ))}
      </nav>
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-slate-200/70 p-5">
        <div
          aria-label={t("editingAria", { title: scene.title })}
          className="relative aspect-video w-full max-w-5xl overflow-hidden bg-white shadow-xl ring-1 ring-slate-300"
          data-testid="authored-layered-canvas"
          style={{ backgroundColor: scene.backgroundColor }}
        >
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-10" data-layer="master">
            {scene.masterElements.map((element) => (
              <div
                className="absolute overflow-hidden"
                data-locked="true"
                key={element.id}
                style={{
                  ...percentBounds(element.bounds),
                  backgroundColor: element.style?.backgroundColor,
                  borderColor: element.style?.borderColor,
                  color: element.style?.color,
                  fontFamily: element.style?.fontFamily,
                  fontSize: element.style?.fontSize,
                  fontWeight: element.style?.fontWeight,
                  textAlign: element.style?.textAlign,
                }}
              >
                {element.kind === "logo" && element.assetUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img alt="" className="h-full w-full object-contain" src={element.assetUrl} />
                ) : "text" in element ? (
                  element.text
                ) : null}
              </div>
            ))}
          </div>
          <div className="absolute inset-0" data-layer="body">
            {scene.bodyElements.map((element) => {
              const selected = element.id === selectedElementId;
              return (
                <div
                  className={`absolute overflow-hidden rounded-sm ${
                    selected ? "ring-2 ring-blue-500 ring-offset-1" : "hover:ring-1 hover:ring-blue-300"
                  }`}
                  data-element-id={element.id}
                  key={element.id}
                  onPointerDown={() => onSelectElement(element.id)}
                  style={percentBounds(element.bounds)}
                >
                  {element.kind === "text" ? (
                    <CompositionSafeTextEditor
                      className="h-full w-full resize-none overflow-hidden border-0 bg-transparent p-1 text-inherit outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed"
                      disabled={disabled || !element.editability.text}
                      label={t("textEditAria", { label: element.label })}
                      onCommit={(text) =>
                        onCommand({
                          clientOperationId: createOperationId(),
                          elementId: element.id,
                          expectedRevision: scene.revision,
                          kind: "text.patch",
                          patch: { text },
                          slideId: scene.slideId,
                        })
                      }
                      value={element.text}
                    />
                  ) : (
                    <button
                      aria-label={t("selectAria", { label: element.label })}
                      className="h-full w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                      disabled={disabled}
                      onClick={() => onSelectElement(element.id)}
                      type="button"
                    >
                      {bodyFallback(element)}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-slate-950/75 px-2 py-1 text-[10px] font-semibold text-white">
            {t("layerStatus")}
          </div>
        </div>
      </div>
    </section>
  );
}
