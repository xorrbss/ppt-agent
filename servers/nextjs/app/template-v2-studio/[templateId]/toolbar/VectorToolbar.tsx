"use client";

import { useState } from "react";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { templateV2EditableVectorGeometry } from "@/lib/template-v2-vector";
import {
  ToolbarField,
  ToolbarSelect,
  toolbarInputClass,
} from "./ToolbarPrimitives";
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
  const [requestedPointIndex, setRequestedPointIndex] = useState(0);
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
  const pointIndex = editableGeometry
    ? Math.min(requestedPointIndex, editableGeometry.points.length - 1)
    : 0;
  const editablePoint = editableGeometry?.points[pointIndex] ?? null;
  const editableCornerRadii =
    editableGeometry &&
    editableGeometry.shape !== "ellipse" &&
    editableGeometry.closed &&
    editableGeometry.points.length >= 3
      ? editableGeometry.cornerRadii
      : null;

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
      {editableGeometry &&
      editableGeometry.shape !== null &&
      editableGeometry.points.length === 2 ? (
        <ToolbarField label="Vector shape">
          <span aria-hidden="true">Shape</span>
          <ToolbarSelect
            className="w-20"
            aria-label="Vector shape"
            value={editableGeometry.shape}
            onChange={(event) => {
              const value = event.target.value;
              if (value !== "polygon" && value !== "ellipse") return;
              onPatch([{ path: ["shape"], value }], {
                historyKey: "toolbar:vector-shape",
              });
            }}
          >
            <option value="polygon">Polygon</option>
            <option value="ellipse">Ellipse</option>
          </ToolbarSelect>
        </ToolbarField>
      ) : null}
      {editableGeometry && editablePoint ? (
        <>
          <ToolbarField label="Vector point">
            <span aria-hidden="true">Point</span>
            <ToolbarSelect
              className="w-14"
              aria-label="Vector point"
              value={pointIndex}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (
                  Number.isInteger(value) &&
                  value >= 0 &&
                  value < editableGeometry.points.length
                ) {
                  setRequestedPointIndex(value);
                }
              }}
            >
              {editableGeometry.points.map((_, index) => (
                <option key={index} value={index}>
                  {index + 1}
                </option>
              ))}
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label="Vector point X">
            <span aria-hidden="true">X</span>
            <input
              className={`${toolbarInputClass} w-20`}
              aria-label="Vector point X"
              type="number"
              step={0.01}
              value={editablePoint.x}
              onChange={(event) => {
                const value = event.target.valueAsNumber;
                if (!Number.isFinite(value)) return;
                onPatch(
                  [{ path: ["points", pointIndex, "x"], value }],
                  {
                    historyKey: `toolbar:vector-point-${pointIndex}-x`,
                    continuous: true,
                  }
                );
              }}
            />
          </ToolbarField>
          <ToolbarField label="Vector point Y">
            <span aria-hidden="true">Y</span>
            <input
              className={`${toolbarInputClass} w-20`}
              aria-label="Vector point Y"
              type="number"
              step={0.01}
              value={editablePoint.y}
              onChange={(event) => {
                const value = event.target.valueAsNumber;
                if (!Number.isFinite(value)) return;
                onPatch(
                  [{ path: ["points", pointIndex, "y"], value }],
                  {
                    historyKey: `toolbar:vector-point-${pointIndex}-y`,
                    continuous: true,
                  }
                );
              }}
            />
          </ToolbarField>
        </>
      ) : null}
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
      {editableGeometry && editableCornerRadii ? (
        <ToolbarField label="Vector corner radius">
          <span aria-hidden="true">Radius</span>
          <input
            className={`${toolbarInputClass} w-20`}
            aria-label="Vector corner radius"
            type="number"
            min={0}
            max={10000}
            step={0.01}
            value={editableCornerRadii[pointIndex] ?? 0}
            onChange={(event) => {
              const value = event.target.valueAsNumber;
              if (
                !Number.isFinite(value) ||
                value < 0 ||
                value > 10000
              ) {
                return;
              }
              if (pointIndex < editableCornerRadii.length) {
                onPatch(
                  [{ path: ["corner_radii", pointIndex], value }],
                  {
                    historyKey: `toolbar:vector-corner-${pointIndex}`,
                    continuous: true,
                  }
                );
                return;
              }
              const values = Array.from(
                { length: editableGeometry.points.length },
                (_, index) =>
                  index === pointIndex
                    ? value
                    : (editableCornerRadii[index] ?? 0)
              );
              onPatch([{ path: ["corner_radii"], value: values }], {
                historyKey: `toolbar:vector-corner-${pointIndex}`,
                continuous: true,
              });
            }}
          />
        </ToolbarField>
      ) : null}
      {editableGeometry && editableGeometry.shape !== "ellipse" ? (
        <ToolbarField label="Smooth vector curve">
          <span aria-hidden="true">Smooth</span>
          <input
            aria-label="Smooth vector curve"
            type="checkbox"
            checked={Boolean(editableCurve)}
            disabled={Boolean(editableCurve && !editableCurve.canRemove)}
            title={
              editableCurve && !editableCurve.canRemove
                ? "Curve extensions are preserved; remove is unavailable."
                : undefined
            }
            onChange={(event) =>
              onPatch(
                [{
                  path: ["curve"],
                  value: event.target.checked
                    ? { type: "smooth", tension: 0.5, segments: 16 }
                    : null,
                }],
                { historyKey: "toolbar:vector-curve" }
              )
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
