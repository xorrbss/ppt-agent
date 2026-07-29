"use client";

import React from "react";
import { useDispatch } from "react-redux";
import { updateAdaptiveBlock } from "@/store/slices/presentationGeneration";

// Edit-mode-only property panel for adaptive slides: surfaces NON-TEXT block
// attributes that have no inline editor. Currently the chart block's `chartType`
// (image/icon replace already have click-to-edit pickers; text is edited via
// Tiptap). Rendered ONLY inside EditableLayoutWrapper (isEditMode) so it never
// appears in the readOnly export DOM. Dispatches the existing updateAdaptiveBlock
// reducer through the generic dotted "<blockId>.<field>" setter
// (lib/adaptiveBlockEdit) — no new store code, export-clean.

type AnyBlock = Record<string, any>;
const CHART_TYPES = ["bar", "line", "area", "pie", "donut"] as const;
const CHART_LABELS: Record<string, string> = {
  bar: "막대", line: "선", area: "영역", pie: "파이", donut: "도넛",
};

interface Props {
  slideIndex: number;
  blocks: AnyBlock[];
}

const AdaptivePropertyControls: React.FC<Props> = ({ slideIndex, blocks }) => {
  const dispatch = useDispatch();
  const charts = (blocks || []).filter((b) => b && b.type === "chart" && b.id);
  if (charts.length === 0) return null;

  return (
    <div
      data-adaptive-property-controls
      className="absolute top-3 left-3 z-30 w-52 rounded-lg border border-gray-200 bg-white/95 p-2 text-sm shadow-lg backdrop-blur"
      contentEditable={false}
    >
      <div className="px-1 pb-1 text-xs font-semibold text-gray-500">속성 편집</div>
      <ul className="flex flex-col gap-2">
        {charts.map((c) => {
          const value = (CHART_TYPES as readonly string[]).includes(c.chartType) ? c.chartType : "bar";
          return (
            <li key={c.id} className="flex flex-col gap-1 rounded px-1 py-0.5">
              <span className="text-xs text-gray-500">차트 종류</span>
              <select
                data-testid={`chart-type-${c.id}`}
                value={value}
                onChange={(e) =>
                  dispatch(updateAdaptiveBlock({ slideIndex, blockId: `${c.id}.chartType`, content: e.target.value }))
                }
                className="rounded border border-gray-200 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-[#5141E5]"
              >
                {CHART_TYPES.map((t) => (
                  <option key={t} value={t}>{CHART_LABELS[t]}</option>
                ))}
              </select>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default AdaptivePropertyControls;
