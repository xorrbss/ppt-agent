"use client";

import { useEffect, useState, type KeyboardEvent } from "react";

import { elementCapabilities } from "@/lib/template-v2-konva";
import {
  currentTemplateV2Geometry,
  updateTemplateV2GeometryField,
  type TemplateV2GeometryField,
} from "@/lib/template-v2-studio-geometry";
import type { ElementGeometry, JsonRecord } from "@/lib/template-v2-studio";

interface TemplateV2GeometryInspectorProps {
  element: JsonRecord;
  disabled: boolean;
  onChange: (geometry: ElementGeometry) => void;
}

const LABELS: Record<TemplateV2GeometryField, string> = {
  x: "X",
  y: "Y",
  width: "Width",
  height: "Height",
  rotation: "Rotation",
};

function geometryDrafts(element: JsonRecord) {
  const geometry = currentTemplateV2Geometry(element);
  return {
    x: String(geometry.x),
    y: String(geometry.y),
    width: String(geometry.width ?? ""),
    height: String(geometry.height ?? ""),
    rotation: String(geometry.rotation ?? ""),
  };
}

export default function TemplateV2GeometryInspector({
  element,
  disabled,
  onChange,
}: TemplateV2GeometryInspectorProps) {
  const capabilities = elementCapabilities(element);
  const [drafts, setDrafts] = useState(() => geometryDrafts(element));

  useEffect(() => {
    setDrafts(geometryDrafts(element));
  }, [element]);

  if (!capabilities.move) return null;

  const fields: TemplateV2GeometryField[] = ["x", "y"];
  if (capabilities.resize) fields.push("width", "height");
  if (capabilities.rotate) fields.push("rotation");

  const reset = () => setDrafts(geometryDrafts(element));
  const commit = (field: TemplateV2GeometryField) => {
    const geometry = updateTemplateV2GeometryField(
      element,
      field,
      drafts[field]
    );
    if (!geometry) {
      reset();
      return;
    }
    setDrafts({
      x: String(geometry.x),
      y: String(geometry.y),
      width: String(geometry.width ?? ""),
      height: String(geometry.height ?? ""),
      rotation: String(geometry.rotation ?? ""),
    });
    onChange(geometry);
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.blur();
    } else if (event.key === "Escape") {
      event.preventDefault();
      reset();
    }
  };

  return (
    <section className="mt-5">
      <p className="text-sm font-medium">Geometry</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {fields.map((field) => (
          <label key={field} className="text-xs text-slate-400">
            {LABELS[field]}
            <input
              type="number"
              step="0.01"
              value={drafts[field]}
              disabled={disabled}
              aria-label={`${LABELS[field]} geometry`}
              onChange={(event) =>
                setDrafts((current) => ({
                  ...current,
                  [field]: event.target.value,
                }))
              }
              onBlur={() => commit(field)}
              onKeyDown={handleKeyDown}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-400 disabled:cursor-not-allowed disabled:opacity-50"
            />
          </label>
        ))}
      </div>
    </section>
  );
}
