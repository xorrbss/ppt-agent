"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import { useTranslations } from "next-intl";

import { CompositionSafeTextEditor } from "./CompositionSafeTextEditor";
import type {
  AssetBodyElement,
  ElementBounds,
  InspectorModel,
  NativeChartType,
  WorkbenchEntityId,
  WorkbenchTypedCommand,
} from "./types";

export interface TypedInspectorProps {
  model: InspectorModel;
  disabled?: boolean;
  createOperationId: () => WorkbenchEntityId;
  onCommand: (command: WorkbenchTypedCommand) => void;
}

type GeometryDraft = Record<keyof ElementBounds, string>;

function canonicalData(value: unknown): unknown {
  if (typeof value === "string") return value.normalize("NFC");
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([key, item]) => [key.normalize("NFC"), canonicalData(item)]));
  if (typeof value === "number" && !Number.isFinite(value)) throw new Error("authored_vnext_scene_data_non_finite");
  return value;
}

const NATIVE_CHART_TYPES: readonly NativeChartType[] = ["bar", "column", "line", "area", "pie", "donut"];

function validateSceneDataPayload(
  kind: Extract<InspectorModel, { kind: "data" }>["nodeKind"],
  payload: Record<string, unknown>,
  chartType: NativeChartType | undefined,
): { rows?: number; columns?: number } {
  const keys = Object.keys(payload).sort();
  if (kind === "table") {
    const cells = payload.cells;
    if (keys.length !== 1 || keys[0] !== "cells" || !Array.isArray(cells) || cells.length < 1 || cells.length > 100 || !cells.every(Array.isArray)) {
      throw new Error("authored_vnext_scene_table_cells_required");
    }
    const columns = cells[0]?.length ?? 0;
    if (columns < 1 || columns > 30 || cells.some((row) => row.length !== columns || row.some((cell) => typeof cell !== "string" || cell.length > 5000))) {
      throw new Error("authored_vnext_scene_table_cells_invalid");
    }
    return { rows: cells.length, columns };
  }
  const categories = payload.categories;
  const series = payload.series;
  if (
    keys.length !== 2 || keys[0] !== "categories" || keys[1] !== "series" ||
    !Array.isArray(categories) || categories.length < 1 || categories.length > 100 ||
    categories.some((category) => typeof category !== "string" || !category || category.length > 500) ||
    !Array.isArray(series) || series.length < 1 || series.length > 30
  ) throw new Error("authored_vnext_scene_chart_data_invalid");
  const names = new Set<string>();
  for (const entry of series) {
    if (
      !entry || typeof entry !== "object" || Array.isArray(entry) ||
      Object.keys(entry as Record<string, unknown>).sort().join(",") !== "name,values" ||
      typeof (entry as { name?: unknown }).name !== "string" || !(entry as { name: string }).name ||
      (entry as { name: string }).name.length > 500 || !Array.isArray((entry as { values?: unknown }).values) ||
      (entry as { values: unknown[] }).values.length !== categories.length ||
      (entry as { values: unknown[] }).values.some((value) => typeof value !== "number" || !Number.isFinite(value))
    ) throw new Error("authored_vnext_scene_chart_series_invalid");
    const name = (entry as { name: string }).name;
    if (names.has(name)) throw new Error("authored_vnext_scene_chart_series_duplicate");
    names.add(name);
  }
  if ((chartType === "pie" || chartType === "donut") && series.length !== 1) {
    throw new Error("authored_vnext_scene_chart_single_series_required");
  }
  return {};
}

async function sceneDataDigest(payload: Record<string, unknown>): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(canonicalData(payload)));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function boundsDraft(bounds: ElementBounds): GeometryDraft {
  return {
    height: String(bounds.height),
    rotation: String(bounds.rotation ?? 0),
    width: String(bounds.width),
    x: String(bounds.x),
    y: String(bounds.y),
  };
}

function NumberField({
  label,
  value,
  disabled,
  onChange,
  onCommit,
}: {
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onCommit: () => void;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.blur();
    }
  };
  return (
    <label className="text-xs font-medium text-slate-600">
      {label}
      <input
        className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm tabular-nums outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
        disabled={disabled}
        inputMode="decimal"
        onBlur={onCommit}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        step="0.01"
        type="number"
        value={value}
      />
    </label>
  );
}

function GeometryInspector({
  model,
  disabled,
  createOperationId,
  onCommand,
}: TypedInspectorProps & { model: Extract<InspectorModel, { kind: "geometry" }> }) {
  const t = useTranslations("authoredVnext.workbench.inspector");
  const [draft, setDraft] = useState(() => boundsDraft(model.bounds));
  useEffect(() => setDraft(boundsDraft(model.bounds)), [model]);

  const commit = (field: keyof ElementBounds) => {
    const value = Number(draft[field]);
    if (!Number.isFinite(value) || value === model.bounds[field]) return;
    onCommand({
      ...model.context,
      clientOperationId: createOperationId(),
      kind: "geometry.patch",
      patch: { [field]: value },
    });
  };

  return (
    <div className="grid grid-cols-2 gap-2">
      {(["x", "y"] as const).map((field) => (
        <NumberField
          disabled={disabled ?? false}
          key={field}
          label={`${field.toUpperCase()} (px)`}
          onChange={(value) => setDraft((current) => ({ ...current, [field]: value }))}
          onCommit={() => commit(field)}
          value={draft[field]}
        />
      ))}
    </div>
  );
}

function AssetInspector({
  model,
  disabled,
  createOperationId,
  onCommand,
}: TypedInspectorProps & { model: Extract<InspectorModel, { kind: "asset" }> }) {
  const t = useTranslations("authoredVnext.workbench.inspector");
  const [assetId, setAssetId] = useState(model.assetId);
  const [assetDigest, setAssetDigest] = useState(model.assetDigest);
  const [alt, setAlt] = useState(model.alt);
  const [crop, setCrop] = useState<AssetBodyElement["crop"]>(model.fit);
  const [mediaType, setMediaType] = useState(model.mediaType ?? "");
  const [storageReference, setStorageReference] = useState(model.storageReference ?? "");
  const [sizeBytes, setSizeBytes] = useState(model.sizeBytes?.toString() ?? "");
  useEffect(() => {
    setAssetId(model.assetId);
    setAssetDigest(model.assetDigest);
    setAlt(model.alt);
    setCrop(model.fit);
    setMediaType(model.mediaType ?? "");
    setStorageReference(model.storageReference ?? "");
    setSizeBytes(model.sizeBytes?.toString() ?? "");
  }, [model]);
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-slate-600">
        {t("assetId")}
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setAssetId(event.target.value)} value={assetId} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        Asset digest
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 font-mono text-xs" disabled={disabled} onChange={(event) => setAssetDigest(event.target.value)} value={assetDigest} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        {t("altText")}
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setAlt(event.target.value)} value={alt} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        {t("fit")}
        <select className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setCrop(event.target.value as AssetBodyElement["crop"])} value={crop}>
          <option value="cover">{t("cover")}</option>
          <option value="contain">{t("contain")}</option>
        </select>
      </label>
      <label className="block text-xs font-medium text-slate-600">
        Media type (required for a new asset)
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setMediaType(event.target.value)} value={mediaType} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        Storage reference (required for a new asset)
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 font-mono text-xs" disabled={disabled} onChange={(event) => setStorageReference(event.target.value)} value={storageReference} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        Verified byte length
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 font-mono text-xs" disabled={disabled} min="1" onChange={(event) => setSizeBytes(event.target.value)} type="number" value={sizeBytes} />
      </label>
      <button
        className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
        disabled={disabled || !assetId.trim() || !assetDigest.trim() || !alt.trim() || !Number.isInteger(Number(sizeBytes)) || Number(sizeBytes) <= 0 || (assetId.trim() !== model.assetId && (!mediaType.trim() || !storageReference.trim()))}
        onClick={() => onCommand({ ...model.context, clientOperationId: createOperationId(), kind: "asset.replace", patch: { alt: alt.trim(), assetId: assetId.trim(), assetDigest: assetDigest.trim(), crop, sizeBytes: Number(sizeBytes), ...(mediaType.trim() ? { mediaType: mediaType.trim() as AssetBodyElement["mediaType"] } : {}), ...(storageReference.trim() ? { storageReference: storageReference.trim() } : {}) } })}
        type="button"
      >
        {t("replaceAsset")}
      </button>
    </div>
  );
}

function DataInspector({ model, disabled, createOperationId, onCommand }: TypedInspectorProps & { model: Extract<InspectorModel, { kind: "data" }> }) {
  const [draft, setDraft] = useState(() => JSON.stringify(model.dataPayload, null, 2));
  const [chartType, setChartType] = useState<NativeChartType | "">(model.chartType ?? "");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setDraft(JSON.stringify(model.dataPayload, null, 2)); setChartType(model.chartType ?? ""); setError(null); }, [model]);
  const commit = async () => {
    try {
      const parsed: unknown = JSON.parse(draft);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("authored_vnext_scene_data_object_required");
      const dataPayload = parsed as Record<string, unknown>;
      const nextChartType = model.nodeKind === "chart" ? chartType : undefined;
      if (model.nodeKind === "chart" && !nextChartType) throw new Error("authored_vnext_scene_chart_type_required");
      const dimensions = validateSceneDataPayload(model.nodeKind, dataPayload, nextChartType || undefined);
      onCommand({ ...model.context, clientOperationId: createOperationId(), kind: "data.set", patch: { dataId: model.dataId, dataDigest: await sceneDataDigest(dataPayload), dataPayload, ...(dimensions.rows === undefined ? {} : { rows: dimensions.rows }), ...(dimensions.columns === undefined ? {} : { columns: dimensions.columns }), ...(nextChartType ? { chartType: nextChartType } : {}), ...(model.accessibilitySummary === undefined ? {} : { accessibilitySummary: model.accessibilitySummary }) } });
      setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "authored_vnext_scene_data_invalid"); }
  };
  return <div className="space-y-3"><p className="text-xs text-slate-600">{model.label} · {model.nodeKind} · <code>{model.dataDigest}</code></p>{model.nodeKind === "chart" && <label className="block text-xs font-medium text-slate-600">Native chart type<select aria-label={`${model.label} chart type`} className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setChartType(event.target.value as NativeChartType)} value={chartType}>{NATIVE_CHART_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}</select></label>}<textarea aria-label={`${model.label} data JSON`} className="min-h-36 w-full rounded-md border border-slate-300 p-2 font-mono text-xs" disabled={disabled} onChange={(event) => setDraft(event.target.value)} value={draft} /><button className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40" disabled={disabled} onClick={() => void commit()} type="button">Apply sealed data</button>{error && <p className="rounded bg-rose-50 p-2 text-xs text-rose-800" role="alert">{error}</p>}{model.geometry && <><p className="pt-2 text-xs font-semibold text-slate-700">Position</p><GeometryInspector createOperationId={createOperationId} disabled={disabled} model={{ kind: "geometry", context: model.context, bounds: model.geometry }} onCommand={onCommand} /></>}</div>;
}

function CapabilityInspector({ model }: { model: Extract<InspectorModel, { kind: "capability" }> }) {
  return (
    <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
      <p className="font-semibold">{model.label}</p>
      <p>{model.message}</p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        {Object.entries(model.editability).map(([capability, enabled]) => (
          <div className="flex justify-between gap-2" key={capability}>
            <dt>{capability}</dt><dd>{enabled ? "supported" : "not supported"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function TypedInspector({
  model,
  disabled = false,
  createOperationId,
  onCommand,
}: TypedInspectorProps) {
  const t = useTranslations("authoredVnext.workbench.inspector");
  return (
    <aside aria-label={t("aria")} className="border-b border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">{t("title")}</h2>
        {model.kind !== "none" && <span className="rounded bg-slate-100 px-2 py-1 text-[10px] font-semibold uppercase text-slate-500">{model.kind}</span>}
      </div>
      {model.kind === "none" && <p className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-sm text-slate-500">{t("empty")}</p>}
      {model.kind === "text" && (
        <CompositionSafeTextEditor
          className="min-h-28 w-full resize-y rounded-md border border-slate-300 p-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
          disabled={disabled}
          label={t("textAria")}
          onCommit={(text) => onCommand({ ...model.context, clientOperationId: createOperationId(), kind: "text.patch", patch: { text } })}
          value={model.text}
        />
      )}
      {model.kind === "geometry" && <GeometryInspector createOperationId={createOperationId} disabled={disabled} model={model} onCommand={onCommand} />}
      {model.kind === "asset" && <AssetInspector createOperationId={createOperationId} disabled={disabled} model={model} onCommand={onCommand} />}
      {model.kind === "data" && <DataInspector createOperationId={createOperationId} disabled={disabled} model={model} onCommand={onCommand} />}
      {model.kind === "capability" && <CapabilityInspector model={model} />}
      {model.kind === "layout" && (
        <div className="space-y-2">
          <label className="block text-xs font-medium text-slate-600">
            {t("layout")}
            <select
              className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm"
              disabled={disabled}
              onChange={(event) => onCommand({ ...model.context, clientOperationId: createOperationId(), kind: "layout.apply", patch: { layoutId: event.target.value } })}
              value={model.layoutId}
            >
              {model.options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
            </select>
          </label>
          <p className="text-xs text-slate-500">{t("layoutDescription")}</p>
        </div>
      )}
    </aside>
  );
}
