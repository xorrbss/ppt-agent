"use client";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { chartContractCapabilities } from "@/lib/template-v2-chart-table-contract";
import { ToolbarButton, ToolbarField, toolbarInputClass } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

export default function ChartToolbar({
  element,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  const capabilities = chartContractCapabilities(element.chart_type);
  const supportsAxes = capabilities.ok && capabilities.value.axisOptions;
  const patchColor = (key: "title_color" | "legend_color" | "axis_color" | "grid_color", value: string) =>
    onPatch([{ path: [key], value }], {
      historyKey: `toolbar:chart-${key}`,
      continuous: true,
    });

  return (
    <div className="flex items-center gap-2">
      <ToolbarField label="Chart title color">
        <span aria-hidden="true">Title</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Chart title color"
          type="text"
          value={typeof element.title_color === "string" ? element.title_color : ""}
          placeholder="#111827"
          onChange={(event) => patchColor("title_color", event.target.value)}
        />
      </ToolbarField>
      <ToolbarButton
        aria-label="Toggle chart legend"
        aria-pressed={element.legend === true}
        onClick={() =>
          onPatch([{ path: ["legend"], value: element.legend !== true }], {
            historyKey: "toolbar:chart-legend",
          })
        }
      >
        Legend
      </ToolbarButton>
      <ToolbarField label="Chart legend color">
        <span aria-hidden="true">Legend</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Chart legend color"
          type="text"
          value={typeof element.legend_color === "string" ? element.legend_color : ""}
          placeholder="#374151"
          onChange={(event) => patchColor("legend_color", event.target.value)}
        />
      </ToolbarField>
      {supportsAxes && (
        <>
          <ToolbarField label="Chart axis color">
            <span aria-hidden="true">Axis</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label="Chart axis color"
              type="text"
              value={typeof element.axis_color === "string" ? element.axis_color : ""}
              placeholder="#6b7280"
              onChange={(event) => patchColor("axis_color", event.target.value)}
            />
          </ToolbarField>
          <ToolbarField label="Chart grid color">
            <span aria-hidden="true">Grid</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label="Chart grid color"
              type="text"
              value={typeof element.grid_color === "string" ? element.grid_color : ""}
              placeholder="#e5e7eb"
              onChange={(event) => patchColor("grid_color", event.target.value)}
            />
          </ToolbarField>
        </>
      )}
      <ToolbarField label="Chart opacity">
        <span aria-hidden="true">Opacity</span>
        <input
          className="w-20 accent-violet-400"
          aria-label="Chart opacity"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numberValue(element.opacity, 1)}
          onChange={(event) =>
            onPatch([{ path: ["opacity"], value: Number(event.target.value) }], {
              historyKey: "toolbar:chart-opacity",
              continuous: true,
            })
          }
        />
      </ToolbarField>
    </div>
  );
}
