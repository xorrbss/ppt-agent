"use client";
import React, { useState } from "react";
import { useDispatch } from "react-redux";
import { Pencil, Plus, Trash2 } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { updateAdaptiveBlockChart } from "@/store/slices/presentationGeneration";
import { useEditableText } from "./EditableTextContext";

type ChartType = "bar" | "line" | "area" | "pie" | "donut";
const CHART_TYPES: { value: ChartType; label: string }[] = [
  { value: "bar", label: "막대" },
  { value: "line", label: "선" },
  { value: "area", label: "영역" },
  { value: "pie", label: "파이" },
  { value: "donut", label: "도넛" },
];

/**
 * Edit affordance for an adaptive chart block. Rendered inside ChartLeaf but only
 * appears in edit mode (read-only / export / thumbnails get null), so it never
 * leaks into the exported DOM. Binds by the block id via updateAdaptiveBlockChart
 * — supports single- and multi-series (`series[]` + per-row `values[]`).
 *
 * The read-only guard (context only) is split from the editor so useDispatch is
 * never called off the edit path — ChartLeaf renders read-only in contexts without
 * a Redux Provider (export, isolated tests) where useDispatch would throw.
 */
const AdaptiveChartControls = ({ block }: { block: any }) => {
  const { isEditMode } = useEditableText();
  if (!isEditMode || !block?.id) return null;
  return <ChartEditor block={block} />;
};

const ChartEditor = ({ block }: { block: any }) => {
  const { slideIndex } = useEditableText();
  const dispatch = useDispatch();
  const [open, setOpen] = useState(false);

  const chartType: ChartType = block.chartType || "bar";
  const series: string[] = Array.isArray(block.series) ? block.series : [];
  const multi = series.length > 1;
  const data: any[] = Array.isArray(block.data) ? block.data : [];

  const commit = (patch: {
    chartType?: string;
    data?: any[];
    series?: string[];
  }) =>
    dispatch(
      updateAdaptiveBlockChart({ slideIndex, blockId: block.id, patch })
    );

  const rowValues = (row: any): number[] =>
    Array.isArray(row?.values) ? row.values : series.map(() => 0);

  const setName = (i: number, name: string) =>
    commit({ data: data.map((r, idx) => (idx === i ? { ...r, name } : r)) });
  const setValue = (i: number, raw: string) =>
    commit({
      data: data.map((r, idx) =>
        idx === i ? { ...r, value: Number(raw) || 0 } : r
      ),
    });
  const setSeriesValue = (i: number, si: number, raw: string) =>
    commit({
      data: data.map((r, idx) =>
        idx === i
          ? {
              ...r,
              values: rowValues(r).map((x, k) =>
                k === si ? Number(raw) || 0 : x
              ),
            }
          : r
      ),
    });
  const setSeriesLabel = (si: number, label: string) =>
    commit({ series: series.map((s, k) => (k === si ? label : s)) });
  const addRow = () =>
    commit({
      data: [
        ...data,
        multi
          ? { name: "새 항목", values: series.map(() => 0) }
          : { name: "새 항목", value: 0 },
      ],
    });
  const removeRow = (i: number) =>
    commit({ data: data.filter((_, idx) => idx !== i) });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          title="차트 편집"
          contentEditable={false}
          className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-full border border-gray-200 bg-white/90 px-2.5 py-1 text-xs font-medium text-[#101323] shadow-sm backdrop-blur duration-300 hover:border-[#5141e5] hover:text-[#5141e5]"
        >
          <Pencil className="h-3 w-3" />
          차트 편집
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[340px] rounded-[18px] p-0"
        contentEditable={false}
      >
        <div className="border-b border-gray-100 px-4 py-3">
          <p className="text-sm font-semibold text-[#101323]">차트 편집</p>
        </div>

        <div className="space-y-3 p-4">
          {/* Chart type */}
          <div className="flex flex-wrap gap-1.5">
            {CHART_TYPES.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => commit({ chartType: option.value })}
                className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium duration-300 ${
                  chartType === option.value
                    ? "border-[#5141e5] bg-violet-50 text-[#5141e5]"
                    : "border-gray-200 text-gray-600 hover:border-gray-300"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>

          {/* Series labels (multi-series only) */}
          {multi && (
            <div className="flex gap-1.5">
              {series.map((s, si) => (
                <input
                  key={si}
                  value={s}
                  onChange={(e) => setSeriesLabel(si, e.target.value)}
                  placeholder={`계열 ${si + 1}`}
                  className="min-w-0 flex-1 rounded-md border border-gray-200 px-2 py-1 text-xs text-[#101323] outline-none focus:border-violet-300"
                />
              ))}
              <span className="w-6 shrink-0" />
            </div>
          )}

          {/* Data rows */}
          <div className="max-h-[240px] space-y-1.5 overflow-y-auto">
            {data.map((row, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <input
                  value={row?.name ?? ""}
                  onChange={(e) => setName(i, e.target.value)}
                  placeholder="이름"
                  className="min-w-0 flex-1 rounded-md border border-gray-200 px-2 py-1 text-xs text-[#101323] outline-none focus:border-violet-300"
                />
                {multi ? (
                  series.map((_, si) => (
                    <input
                      key={si}
                      type="number"
                      value={rowValues(row)[si] ?? 0}
                      onChange={(e) => setSeriesValue(i, si, e.target.value)}
                      className="w-16 rounded-md border border-gray-200 px-2 py-1 text-xs text-[#101323] outline-none focus:border-violet-300"
                    />
                  ))
                ) : (
                  <input
                    type="number"
                    value={row?.value ?? 0}
                    onChange={(e) => setValue(i, e.target.value)}
                    className="w-20 rounded-md border border-gray-200 px-2 py-1 text-xs text-[#101323] outline-none focus:border-violet-300"
                  />
                )}
                <button
                  type="button"
                  title="삭제"
                  onClick={() => removeRow(i)}
                  disabled={data.length <= 1}
                  className="shrink-0 text-gray-400 duration-300 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>

          <button
            type="button"
            onClick={addRow}
            className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-gray-300 py-1.5 text-xs font-medium text-gray-600 duration-300 hover:border-[#5141e5] hover:text-[#5141e5]"
          >
            <Plus className="h-3.5 w-3.5" />
            데이터 추가
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default AdaptiveChartControls;
