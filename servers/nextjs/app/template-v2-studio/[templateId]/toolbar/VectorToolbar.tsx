"use client";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { templateV2EditableVectorGeometry } from "@/lib/template-v2-vector";
import { ToolbarField, toolbarInputClass } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const record = (value: unknown): TemplateV2ToolbarRecord | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as TemplateV2ToolbarRecord)
    : null;

export default function VectorToolbar({
  element,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  const fill = record(element.fill);
  const stroke = record(element.stroke);
  const fillColor = fill
    ? typeof fill.color === "string"
      ? fill.color
      : ""
    : typeof element.fill === "string"
      ? element.fill
      : "";
  const strokeColor = typeof stroke?.color === "string" ? stroke.color : "";
  const editableGeometry = templateV2EditableVectorGeometry(element);
  const editableCurve =
    editableGeometry?.shape === "ellipse"
      ? null
      : (editableGeometry?.curve ?? null);

  function patchFill(value: string) {
    const path = fill || element.fill == null ? ["fill", "color"] : ["fill"];
    onPatch(
      [{ path, value }],
      { historyKey: "toolbar:vector-fill", continuous: true }
    );
  }

  return (
    <div className="flex items-center gap-2">
      <ToolbarField label="Shape fill color">
        <span aria-hidden="true">Fill</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Shape fill color"
          type="text"
          value={fillColor}
          placeholder="#000000"
          onChange={(event) => patchFill(event.target.value)}
        />
      </ToolbarField>
      <ToolbarField label="Shape stroke color">
        <span aria-hidden="true">Stroke</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Shape stroke color"
          type="text"
          value={strokeColor}
          placeholder="#000000"
          onChange={(event) =>
            onPatch([{ path: ["stroke", "color"], value: event.target.value }], {
              historyKey: "toolbar:vector-stroke-color",
              continuous: true,
            })
          }
        />
      </ToolbarField>
      <ToolbarField label="Shape stroke width">
        <span aria-hidden="true">Width</span>
        <input
          className={`${toolbarInputClass} w-16`}
          aria-label="Shape stroke width"
          type="number"
          min={0}
          max={100}
          step={0.5}
          value={numberValue(stroke?.width, 0)}
          onChange={(event) =>
            onPatch(
              [{
                path: ["stroke", "width"],
                value: Math.min(
                  100,
                  Math.max(0, Number(event.target.value) || 0)
                ),
              }],
              { historyKey: "toolbar:vector-stroke-width", continuous: true }
            )
          }
        />
      </ToolbarField>
      <ToolbarField label="Shape opacity">
        <span aria-hidden="true">Opacity</span>
        <input
          className="w-20 accent-violet-400"
          aria-label="Shape opacity"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numberValue(element.opacity, 1)}
          onChange={(event) =>
            onPatch([{ path: ["opacity"], value: Number(event.target.value) }], {
              historyKey: "toolbar:vector-opacity",
              continuous: true,
            })
          }
        />
      </ToolbarField>
      {editableGeometry && editableGeometry.shape !== "ellipse" ? (
        <ToolbarField label="Closed vector path">
          <span aria-hidden="true">Closed</span>
          <input
            aria-label="Closed vector path"
            type="checkbox"
            checked={editableGeometry.closed}
            onChange={(event) =>
              onPatch([{ path: ["closed"], value: event.target.checked }], {
                historyKey: "toolbar:vector-closed",
              })
            }
          />
        </ToolbarField>
      ) : null}
      {editableCurve ? (
        <>
          <ToolbarField label="Curve tension">
            <span aria-hidden="true">Tension</span>
            <input
              className="w-20 accent-violet-400"
              aria-label="Curve tension"
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={editableCurve.tension}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (!Number.isFinite(value) || value < 0 || value > 1) return;
                onPatch([{ path: ["curve", "tension"], value }], {
                  historyKey: "toolbar:vector-curve-tension",
                  continuous: true,
                });
              }}
            />
          </ToolbarField>
          <ToolbarField label="Curve segments">
            <span aria-hidden="true">Segments</span>
            <input
              className={`${toolbarInputClass} w-16`}
              aria-label="Curve segments"
              type="number"
              min={1}
              max={96}
              step={1}
              value={editableCurve.segments}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (
                  !Number.isInteger(value) ||
                  value < 1 ||
                  value > 96
                ) {
                  return;
                }
                onPatch([{ path: ["curve", "segments"], value }], {
                  historyKey: "toolbar:vector-curve-segments",
                  continuous: true,
                });
              }}
            />
          </ToolbarField>
        </>
      ) : null}
    </div>
  );
}
