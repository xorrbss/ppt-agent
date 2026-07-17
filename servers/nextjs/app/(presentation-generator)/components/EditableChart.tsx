"use client";
import React, { useState } from "react";
import { useDispatch } from "react-redux";
import { Pencil, Plus, Trash2 } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { GeneralChart } from "@/app/presentation-templates/financial-chart/GeneralChartPrimitives";
import { updateSlideChart } from "@/store/slices/presentationGeneration";
import { useEditableText } from "./EditableTextContext";

type ChartType = "bar" | "line" | "area";
interface ChartPoint {
  name?: string;
  value?: number;
}
export interface ChartData {
  type?: ChartType;
  data: ChartPoint[];
}

const CHART_TYPES: { value: ChartType; label: string }[] = [
  { value: "bar", label: "막대" },
  { value: "line", label: "선" },
  { value: "area", label: "영역" },
];

/**
 * In-tree editable chart — renders the (canvas) chart read-only, and in edit mode
 * overlays a popover to change the chart type and edit / add / remove data points.
 * Adding/removing points changes the array shape and values must stay numeric, so
 * it commits the whole chart object via updateSlideChart (not the text-only
 * updateSlideContent). Consistent with <EditableText>: editing lives in the tree,
 * no DOM surgery.
 */
const EditableChart = ({
  chart,
  showLegend = false,
  showTooltip = false,
}: {
  chart: ChartData;
  showLegend?: boolean;
  showTooltip?: boolean;
}) => {
  const { slideIndex, isEditMode } = useEditableText();
  const dispatch = useDispatch();
  const [open, setOpen] = useState(false);

  const type: ChartType = chart.type ?? "bar";
  const points = chart.data ?? [];

  const commit = (next: ChartData) =>
    dispatch(updateSlideChart({ slideIndex, chart: next }));

  const setType = (nextType: ChartType) => commit({ ...chart, type: nextType });
  const setPoint = (index: number, key: keyof ChartPoint, raw: string) =>
    commit({
      ...chart,
      data: points.map((point, i) =>
        i === index
          ? { ...point, [key]: key === "value" ? Number(raw) || 0 : raw }
          : point
      ),
    });
  const addPoint = () =>
    commit({ ...chart, data: [...points, { name: "새 항목", value: 0 }] });
  const removePoint = (index: number) =>
    commit({ ...chart, data: points.filter((_, i) => i !== index) });

  const chartEl = (
    <GeneralChart
      type={type}
      data={points}
      showLegend={showLegend}
      showTooltip={showTooltip}
    />
  );

  if (!isEditMode) return chartEl;

  return (
    <div className="relative h-full w-full">
      {chartEl}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            title="차트 편집"
            data-editable-native
            className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-full border border-gray-200 bg-white/90 px-2.5 py-1 text-xs font-medium text-[#101323] shadow-sm backdrop-blur duration-300 hover:border-[#5141e5] hover:text-[#5141e5]"
          >
            <Pencil className="h-3 w-3" />
            차트 편집
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[300px] rounded-[18px] p-0">
          <div className="border-b border-gray-100 px-4 py-3">
            <p className="text-sm font-semibold text-[#101323]">차트 편집</p>
          </div>

          <div className="space-y-3 p-4">
            {/* Chart type */}
            <div className="flex gap-1.5">
              {CHART_TYPES.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setType(option.value)}
                  className={`flex-1 rounded-lg border px-2 py-1.5 text-xs font-medium duration-300 ${
                    type === option.value
                      ? "border-[#5141e5] bg-violet-50 text-[#5141e5]"
                      : "border-gray-200 text-gray-600 hover:border-gray-300"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>

            {/* Data points */}
            <div className="max-h-[240px] space-y-1.5 overflow-y-auto">
              {points.map((point, index) => (
                <div key={index} className="flex items-center gap-1.5">
                  <input
                    value={point.name ?? ""}
                    onChange={(e) => setPoint(index, "name", e.target.value)}
                    placeholder="이름"
                    className="min-w-0 flex-1 rounded-md border border-gray-200 px-2 py-1 text-xs text-[#101323] outline-none focus:border-violet-300"
                  />
                  <input
                    type="number"
                    value={point.value ?? 0}
                    onChange={(e) => setPoint(index, "value", e.target.value)}
                    className="w-20 rounded-md border border-gray-200 px-2 py-1 text-xs text-[#101323] outline-none focus:border-violet-300"
                  />
                  <button
                    type="button"
                    title="삭제"
                    onClick={() => removePoint(index)}
                    disabled={points.length <= 1}
                    className="shrink-0 text-gray-400 duration-300 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>

            <button
              type="button"
              onClick={addPoint}
              className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-gray-300 py-1.5 text-xs font-medium text-gray-600 duration-300 hover:border-[#5141e5] hover:text-[#5141e5]"
            >
              <Plus className="h-3.5 w-3.5" />
              데이터 추가
            </button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default EditableChart;
