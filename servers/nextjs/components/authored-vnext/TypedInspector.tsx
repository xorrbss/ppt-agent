"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import { useTranslations } from "next-intl";

import { CompositionSafeTextEditor } from "./CompositionSafeTextEditor";
import type {
  AssetBodyElement,
  ElementBounds,
  InspectorModel,
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
      {(["x", "y", "width", "height", "rotation"] as const).map((field) => (
        <NumberField
          disabled={disabled ?? false}
          key={field}
          label={field === "rotation" ? t("rotation") : t("coordinate", { field: field.toUpperCase() })}
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
  const [alt, setAlt] = useState(model.alt);
  const [fit, setFit] = useState<AssetBodyElement["fit"]>(model.fit);
  useEffect(() => {
    setAssetId(model.assetId);
    setAlt(model.alt);
    setFit(model.fit);
  }, [model]);
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-slate-600">
        {t("assetId")}
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setAssetId(event.target.value)} value={assetId} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        {t("altText")}
        <input className="mt-1 w-full rounded-md border border-slate-300 px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setAlt(event.target.value)} value={alt} />
      </label>
      <label className="block text-xs font-medium text-slate-600">
        {t("fit")}
        <select className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm" disabled={disabled} onChange={(event) => setFit(event.target.value as AssetBodyElement["fit"])} value={fit}>
          <option value="cover">{t("cover")}</option>
          <option value="contain">{t("contain")}</option>
        </select>
      </label>
      <button
        className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
        disabled={disabled || !assetId.trim() || !alt.trim()}
        onClick={() => onCommand({ ...model.context, clientOperationId: createOperationId(), kind: "asset.replace", patch: { alt: alt.trim(), assetId: assetId.trim(), fit } })}
        type="button"
      >
        {t("replaceAsset")}
      </button>
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
